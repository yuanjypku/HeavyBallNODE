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
time_rescale = 1.0


class Vdiff(nn.Module):
    def __init__(self):
        super(Vdiff, self).__init__()
        self.osize = 1

    def forward(self, t, x, v):
        truev = v2_vfunc(t)
        return torch.norm(v[:, 0] - truev, 1)


def v1_func(time):
    t1 = torch.clamp(torch.floor(time), 0, len(v1_data) - 1).type(torch.long)
    delta = time - t1
    return v1_data[t1] + delta * (v1_data[t1 + 1] - v1_data[t1])


def v2_vfunc(time):
    t1 = torch.clamp(torch.floor(time), 0, len(v1_data) - 1).type(torch.long)
    return v1_data[t1 + 1] - v1_data[t1]


class initial_velocity(nn.Module):

    def __init__(self, in_channels, out_channels, ddim, zpad=0):
        super(initial_velocity, self).__init__()
        self.fc1 = nn.Linear(in_channels, out_channels * ddim - 0 * in_channels, bias=False)
        self.ddim = ddim
        self.zpad = zpad

    def forward(self, x0):
        out = self.fc1(torch.ones_like(x0))
        # out = torch.cat([x0, out], dim=1)
        out = rearrange(out, 'b (d c) ... -> b d c ...', d=self.ddim)
        if self.zpad > 0:
            out = torch.cat([out, torch.zeros_like(out[:, :self.zpad])], dim=1)
        return out


class DF(nn.Module):

    def __init__(self, in_channels, out_channels=None):
        super(DF, self).__init__()
        out_channels = in_channels if out_channels is None else out_channels
        self.fc1 = nn.Linear(2 * in_channels + 1, out_channels)
        self.act = nn.ReLU(inplace=False)

    def forward(self, t, x):
        v1 = v1_func(t).reshape(-1, 1, 1)
        x = rearrange(x, 'b d c -> b 1 (d c)')
        z_ = torch.cat((x, 0.01 * x ** 3, v1), dim=2)
        out = self.fc1(z_)
        return out


# from torchdiffeq import odeint
dim = 1
hbnodeparams = {
    'thetaact': nn.Identity(),
    'gamma_correction': 0.5,
}
torch.manual_seed(8)
# hbnode = HeavyBallNODE(DF(dim), **hbnodeparams)
# model = NODEintegrate(hbnode, initial_velocity(1, dim, 2), tol=args.tol, adjoint=args.adjoint).to(0)
hbnode = HeavyBallNODE(DF(dim), **hbnodeparams)
nint = NODEintegrate(hbnode, shape=[2, 1], recf=Vdiff(), tol=args.tol, adjoint=args.adjoint)
model = nn.Sequential(initial_velocity(1, dim, 2), nint).to(0)
model_dict = model.state_dict()
for i in model_dict:
    if i != '1.df.df.gamma':
        model_dict[i] *= 0
model.load_state_dict(model_dict)
optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=0.00)
lrscheduler = torch.optim.lr_scheduler.StepLR(optimizer, 10, 0.9)
print('Number of Parameters:', count_parameters(model))
MSELoss = nn.MSELoss()

def TVLoss(input_tensor_1, input_tensor_2):
    tlen = input_tensor_1.shape[0]
    error_tensor = input_tensor_1 - input_tensor_2
    diff_tensor = (torch.roll(error_tensor, -1, 0) - error_tensor)[:-1]
    diff_norm = torch.norm(diff_tensor, p=1) / (tlen - 1)
    return diff_norm


def train(trsz):
    model[1].df.nfe = 0
    model[1].evaluation_times = torch.arange(trsz * 1.0) / time_rescale
    predict, rec = model(v2_data[:1].reshape(1, 1))
    predict = predict.view(trsz, -1)
    mseloss = MSELoss(predict[:, 0], v2_data[:trsz])
    tvloss = rec * 0.001
    loss = mseloss + 0.3 * tvloss
    loss.backward()
    loss, mseloss, tvloss = to_float([loss, mseloss, tvloss], 5)
    nn.utils.clip_grad_norm_(model.parameters(), 10.0)
    optimizer.step()
    timelist.append(time.time())
    return predict, loss, (mseloss, tvloss)


recattrname = ['epoch', 'loss', 'mse', 'tvloss', 'nfe', 'floss', 'time', 'gamma']


def validation(trsz, tssz, plotrange=1000):
    model[1].evaluation_times = (trsz + torch.arange(tssz)) / time_rescale
    forecast = model(v2_data[:1].reshape(1, 1))[0].view(tssz, -1)[:, 0]
    floss = MSELoss(forecast, v2_data[trsz:trsz + tssz])
    plt.plot(v2_data[:plotrange].detach().cpu())
    plt.plot(predict[:plotrange, 0].detach().cpu())
    plt.show()
    timelist.append(time.time())
    return floss


timelist = [time.time()]

# train start
for epoch in range(args.niters):
    predict, loss, (mseloss, tvloss) = train(trsz=1000)
    floss = None
    if (epoch + 1) % 10 == 0 or epoch == 0:
        floss = validation(trsz=1000, tssz=3000)
    dtime = to_float(timelist[-1] - timelist[-2], 5)
    gamma = to_float(model[1].df.df.gamma, 5)
    floss = to_float(floss, 5)
    print(str_rec(recattrname, [epoch, loss, mseloss, tvloss, model[1].df.nfe, floss, dtime, gamma]))
    print(model[1].df.df.df.fc1.weight.detach())