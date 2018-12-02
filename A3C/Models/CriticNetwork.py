import torch
import torch.nn as nn
import torch.nn.functional as F


# code from https://github.com/ikostrikov/pytorch-a3c/blob/master/model.py


def init_weights(m):
    if isinstance(m, nn.Linear):
        nn.init.xavier_normal_(m.weight.data)
        m.bias.data.fill_(0)


class CriticNetwork(torch.nn.Module):
    def __init__(self, n_inputs, action_space, is_discrete=False):
        super(CriticNetwork, self).__init__()

        self.is_discrete = is_discrete
        self.action_space = action_space

        self.n_outputs = action_space.shape[0]
        self.n_inputs = n_inputs

        n_hidden = 200

        self.n_inputs = self.n_inputs

        self.hidden_value1 = nn.Linear(self.n_inputs, n_hidden)
        self.hidden_value2 = nn.Linear(n_hidden, n_hidden)
        # self.hidden_value3 = nn.Linear(n_hidden, n_hidden)
        self.value = nn.Linear(n_hidden, 1)

        self.apply(init_weights)
        self.train()

    def forward(self, inputs):
        """
        Defines the forward pass of the network.

        :param inputs: Input array object which sufficiently represents the full state of the environment.
        :return: value
        """

        inputs = inputs.float()

        value_hidden = F.relu(self.hidden_value1(inputs))
        value_hidden = F.relu(self.hidden_value2(value_hidden))
        # value_hidden = F.relu(self.hidden_value3(value_hidden))
        value = self.value(value_hidden)

        return value