# @Time   : 2020/11/17
# @Author : Xiaoxuan Hu
# @Email  : huxiaoxuan@ruc.edu.cn


import torch
import torch.nn as nn
import torch.nn.functional as F
from textbox.model.abstract_generator import UnconditionalGenerator


class MaliGANDiscriminator(UnconditionalGenerator):
    def __init__(self, config, dataset):
        super(MaliGANDiscriminator, self).__init__(config, dataset)

        self.hidden_size = config['hidden_size']
        self.embedding_size = config['discriminator_embedding_size']
        self.max_length = config['max_seq_length'] + 2
        self.num_dis_layers = config['num_dis_layers']
        self.dropout_rate = config['dropout_rate']

        self.pad_idx = dataset.padding_token_idx
        self.vocab_size = dataset.vocab_size

        self.LSTM = nn.LSTM(self.embedding_size, self.hidden_size, self.num_dis_layers, batch_first=True)
        self.word_embedding = nn.Embedding(self.vocab_size, self.embedding_size, padding_idx = self.pad_idx)
        self.vocab_projection = nn.Linear(self.hidden_size, self.vocab_size)

        self.hidden_linear = nn.Linear(self.num_dis_layers * self.hidden_size, self.hidden_size)
        self.label_linear = nn.Linear(self.hidden_size, 1)
        self.dropout = nn.Dropout(self.dropout_rate)

    def forward(self, data):
        """
        Get final predictions of discriminator
        :param input: batch_size * seq_len
        :return: batch_size
        """
        data_embedding = self.word_embedding(data)  # b * l * e
        _, (hidden, _) = self.LSTM(data_embedding)   # hidden: b * num_layers * h
        out = self.hidden_linear(hidden.view(-1, self.num_dis_layers * self.hidden_size))  # b * (num_layers * h) -> b * h
        pred = self.label_linear(self.dropout(torch.tanh(out))).squeeze(1)  # b * h -> b
        pred = torch.sigmoid(pred)
        return pred

    def calculate_loss(self, real_data, fake_data):
        real_y = self.forward(real_data)  # b * l --> b
        fake_y = self.forward(fake_data)
        logits = torch.cat((real_y, fake_y), dim=0)

        real_label = torch.ones_like(real_y)
        fake_label = torch.zeros_like(fake_y)
        target = torch.cat((real_label, fake_label), dim=0)

        loss = F.binary_cross_entropy(logits, target)
        return loss