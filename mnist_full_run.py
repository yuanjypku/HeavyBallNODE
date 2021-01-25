import argparse
import time

import torch.optim as optim

from anode_data_loader import mnist
from base import *

parser = argparse.ArgumentParser()
parser.add_argument('--tol', type=float, default=1e-3)
parser.add_argument('--adjoint', type=eval, default=False)
parser.add_argument('--visualize', type=eval, default=True)
parser.add_argument('--niters', type=int, default=40)
parser.add_argument('--lr', type=float, default=0.001)
parser.add_argument('--gpu', type=int, default=0)
args = parser.parse_args()


# shape: [time, batch, derivatives, channel, x, y]


class anode_initial_velocity(nn.Module):

    def __init__(self, in_channels, aug):
        super(anode_initial_velocity, self).__init__()
        self.aug = aug
        self.in_channels = in_channels

    def forward(self, x0):
        x0 = rearrange(x0.float(), 'b c x y -> b 1 c x y')
        outshape = list(x0.shape)
        outshape[2] = self.aug
        out = torch.zeros(outshape).to(args.gpu)
        out[:, :, :1] += x0
        return out


class hbnode_initial_velocity(nn.Module):

    def __init__(self, in_channels, out_channels, nhid):
        super(hbnode_initial_velocity, self).__init__()
        assert (3 * out_channels >= in_channels)
        self.actv = nn.LeakyReLU(0.3)
        self.fc1 = nn.Conv2d(in_channels, nhid, kernel_size=1, padding=0)
        self.fc2 = nn.Conv2d(nhid, nhid, kernel_size=3, padding=1)
        self.fc3 = nn.Conv2d(nhid, 2 * out_channels - in_channels, kernel_size=1, padding=0)
        self.out_channels = out_channels
        self.in_channels = in_channels

    def forward(self, x0):
        x0 = x0.float()
        out = self.fc1(x0)
        out = self.actv(out)
        out = self.fc2(out)
        out = self.actv(out)
        out = self.fc3(out)
        out = torch.cat([x0, out], dim=1)
        out = rearrange(out, 'b (d c) ... -> b d c ...', d=2)
        return out


class DF(nn.Module):

    def __init__(self, in_channels, nhid, out_channels=None):
        super(DF, self).__init__()
        if out_channels is None:
            out_channels = in_channels
        self.activation = nn.ReLU(inplace=True)
        self.fc1 = nn.Conv2d(in_channels + 1, nhid, kernel_size=1, padding=0)
        self.fc2 = nn.Conv2d(nhid + 1, nhid, kernel_size=3, padding=1)
        self.fc3 = nn.Conv2d(nhid + 1, out_channels, kernel_size=1, padding=0)

    def forward(self, t, x0):
        x0 = rearrange(x0, 'b d c x y -> b (d c) x y')
        t_img = torch.ones_like(x0[:, :1, :, :]).to(device=args.gpu) * t
        out = torch.cat([x0, t_img], dim=1)
        out = self.fc1(out)
        out = self.activation(out)
        out = torch.cat([out, t_img], dim=1)
        out = self.fc2(out)
        out = self.activation(out)
        out = torch.cat([out, t_img], dim=1)
        out = self.fc3(out)
        out = rearrange(out, 'b c x y -> b 1 c x y')
        return out


class predictionlayer(nn.Module):
    def __init__(self, in_channels, truncate=False):
        super(predictionlayer, self).__init__()
        self.dense = nn.Linear(in_channels * 28 * 28, 10)
        self.truncate = truncate

    def forward(self, x):
        if self.truncate:
            x = rearrange(x[:, 0], 'b ... -> b (...)')
        else:
            x = rearrange(x, 'b ... -> b (...)')
        x = self.dense(x)
        return x


trdat, tsdat = mnist()


def model_gen(name):
    if name == 'node':
        dim = 1
        nhid = 92
        layer = NODElayer(NODE(DF(dim, nhid)))
        model = nn.Sequential(anode_initial_velocity(1, dim),
                              layer, predictionlayer(dim))
    elif name == 'anode':
        dim = 6
        nhid = 64
        layer = NODElayer(NODE(DF(dim, nhid)))
        model = nn.Sequential(anode_initial_velocity(1, dim),
                              layer, predictionlayer(dim))
    elif name == 'sonode':
        dim = 1
        nhid = 65
        hblayer = NODElayer(SONODE(DF(2 * dim, nhid, dim)))
        model = nn.Sequential(hbnode_initial_velocity(1, dim, nhid),
                              hblayer, predictionlayer(dim, truncate=True)).to(device=args.gpu)
    elif name == 'sonode2':
        dim = 5
        nhid = 50
        hblayer = NODElayer(SONODE(DF(2 * dim, nhid, dim)))
        model = nn.Sequential(hbnode_initial_velocity(1, dim, nhid),
                              hblayer, predictionlayer(dim, truncate=True)).to(device=args.gpu)
    elif name == 'hbnode':
        dim = 5
        nhid = 50
        layer = NODElayer(HeavyBallNODE(DF(dim, nhid), None))
        model = nn.Sequential(hbnode_initial_velocity(1, dim, nhid),
                              layer, predictionlayer(dim, truncate=True)).to(device=args.gpu)
    else:
        print('model {} not supported.'.format(name))
        model = None
    return model.to(args.gpu)


names = ['node', 'anode', 'sonode', 'sonode2', 'hbnode']

runnum = 'hb'
log = open('./data/_{}.txt'.format(runnum), 'a')
datname = open('./data/mnist_dat_{}.txt'.format(runnum), 'wb')
dat = []
for i in range(5):
    for name in ['hbnode']:
        model = model_gen(name)
        print(name, count_parameters(model), *[count_parameters(i) for i in model])
        optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=0.000)
        train_out = train(model, optimizer, trdat, tsdat, args, evalfreq=1, stdout=log)
        dat.append([name, i, train_out])

import pickle

pickle.dump(dat, datname)
