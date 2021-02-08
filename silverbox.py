from base import *
from sonode_data_loader import load_data

parser = ArgumentParser()
parser.add_argument('--tol', type=float, default=1e-3)
parser.add_argument('--adjoint', type=eval, default=False)
parser.add_argument('--visualise', type=eval, default=True)
parser.add_argument('--niters', type=int, default=1000)
parser.add_argument('--lr', type=float, default=0.01)
parser.add_argument('--gpu', type=int, default=0)
parser.add_argument('--npoints', type=int, default=1000)
parser.add_argument('--experiment_no', type=int, default=1)
args = parser.parse_args()

v1_data, v2_data = load_data('./data/sb.csv', skiprows=1, usecols=(0, 1), rescaling=100)


def v1_func(time):
    if (time > len(v1_data) - 1) or (time < 0):
        return torch.zeros_like(v1_data[0])
    else:
        t1 = torch.floor(time).type(torch.long)
        delta = time - t1
        if delta == 0:
            return v1_data[t1]
        else:
            return v1_data[t1] + delta * (v1_data[t1 + 1] - v1_data[t1])


class initial_velocity(nn.Module):

    def __init__(self, in_channels, out_channels, ddim):
        super(initial_velocity, self).__init__()
        self.fc1 = nn.Linear(in_channels, out_channels * ddim - in_channels)
        self.ddim = ddim

    def forward(self, x0):
        out = self.fc1(x0)
        out = torch.cat([x0, out], dim=1)
        out = rearrange(out, 'b (d c) ... -> b d c ...', d=self.ddim)
        return out


class DF(nn.Module):

    def __init__(self, dim):
        super(DF, self).__init__()
        self.elu = nn.ELU(inplace=False)
        self.fc = nn.Linear(3 * dim, dim)
        self.nfe = 0

    def forward(self, t, x):
        v1 = v1_func(t).reshape(1, 1, -1)
        z_ = torch.cat((x, 0.01 * x ** 3, v1), dim=1)
        z_ = rearrange(z_, 'b d c -> b 1 (d c)')
        out = self.fc(z_)
        return out


# from torchdiffeq import odeint

model = NODEintegrate(HeavyBallNODE(DF(1), None), initial_velocity(1, 1, 2)).to(0)
model_dict = model.state_dict()
# for i in model_dict:
# model_dict[i] *= 0.01
# model_dict[i] -= 0.2
model.load_state_dict(model_dict)
optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=0.00)
criteria = nn.MSELoss()
timelist = [time.time()]
for epoch in range(300):
    model.df.nfe = 0
    if epoch < 30:
        trsz = 10
    elif epoch < 60:
        trsz = 100
    else:
        trsz = 1000
    predict = model(None, torch.arange(trsz * 1.0), v2_data[:1].view(1, 1)).view(trsz, -1)[:, 0]
    loss = criteria(predict, v2_data[:trsz])
    loss.backward()
    loss = loss.detach().cpu().numpy()
    nn.utils.clip_grad_norm_(model.parameters(), 10.0)
    optimizer.step()
    timelist.append(time.time())
    if (epoch + 1) % 10 == 0:
        tssz = 3000
        forecast = model(None, trsz + torch.arange(tssz * 1.0), v2_data[:1].view(1, 1)).view(tssz, -1)[:, 0]
        floss = criteria(forecast, v2_data[trsz:trsz + tssz])
        print(str_rec(['epoch', 'loss', 'nfe', 'floss', 'time', 'gamma'],
                      [epoch, loss, model.df.nfe, floss, timelist[-1] - timelist[-2],
                       model.df.gamma.detach().cpu().numpy()]))
        print(model.df.df.fc.weight)
    else:
        print(str_rec(['epoch', 'loss', 'nfe', 'time', 'gamma'],
                      [epoch, loss, model.df.nfe, timelist[-1] - timelist[-2], model.df.gamma.detach().cpu().numpy()]))

from matplotlib import pyplot as plt

trange = torch.arange(tssz + trsz * 1.0)
plt.plot(trange, v2_data[:len(trange)].cpu())
plt.plot(trange, model(None, trange, v2_data[:1].view(1, 1)).view(len(trange), -1)[:, 0].detach().cpu())
plt.show()
