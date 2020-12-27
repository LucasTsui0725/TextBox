# @Time   : 2020/11/14
# @Author : Junyi Li
# @Email  : lijunyi@ruc.edu.cn

import torch
import torch.nn as nn
import torch.nn.functional as F

from textbox.model.abstract_generator import ConditionalGenerator
from textbox.module.Encoder.transformer_encoder import TransformerEncoder
from textbox.module.Decoder.transformer_decoder import TransformerDecoder
from textbox.module.Embedder.position_embedder import LearnedPositionalEmbedding, SinusoidalPositionalEmbedding
from textbox.module.Attention.attention_mechanism import SelfAttentionMask
from textbox.model.init import xavier_normal_initialization
from textbox.module.strategy import topk_sampling


class TransformerEncDec(ConditionalGenerator):
    r"""Transformer-based Encoder-Decoder architecture is a powerful framework for conditional text generation.

    Reference:
        https://arxiv.org/abs/1706.03762
    """

    def __init__(self, config, dataset):
        super(TransformerEncDec, self).__init__(config, dataset)

        # load parameters info
        self.embedding_size = config['embedding_size']
        self.ffn_size = config['ffn_size']
        self.num_heads = config['num_heads']
        self.num_enc_layers = config['num_enc_layers']
        self.num_dec_layers = config['num_dec_layers']
        self.attn_dropout_ratio = config['attn_dropout_ratio']
        self.attn_weight_dropout_ratio = config['attn_weight_dropout_ratio']
        self.ffn_dropout_ratio = config['ffn_dropout_ratio']

        self.padding_token_idx = dataset.padding_token_idx
        self.sos_token_idx = dataset.sos_token_idx
        self.eos_token_idx = dataset.eos_token_idx

        # define layers and loss
        self.source_token_embedder = nn.Embedding(self.source_vocab_size, self.embedding_size,
                                                  padding_idx=self.padding_token_idx)

        if config['share_vocab']:
            self.target_token_embedder = self.source_token_embedder
        else:
            self.target_token_embedder = nn.Embedding(self.target_vocab_size, self.embedding_size,
                                                      padding_idx=self.padding_token_idx)

        if config['learned_position_embedder']:
            self.position_embedder = LearnedPositionalEmbedding(self.embedding_size)
        else:
            self.position_embedder = SinusoidalPositionalEmbedding(self.embedding_size)

        self.self_attn_mask = SelfAttentionMask()

        self.encoder = TransformerEncoder(self.embedding_size, self.ffn_size, self.num_enc_layers, self.num_heads,
                                          self.attn_dropout_ratio, self.attn_weight_dropout_ratio,
                                          self.ffn_dropout_ratio)

        self.decoder = TransformerDecoder(self.embedding_size, self.ffn_size, self.num_dec_layers, self.num_heads,
                                          self.attn_dropout_ratio, self.attn_weight_dropout_ratio,
                                          self.ffn_dropout_ratio, with_external=True)

        self.vocab_linear = nn.Linear(self.embedding_size, self.target_vocab_size)

        self.loss = nn.CrossEntropyLoss(ignore_index=self.padding_token_idx, reduction='none')
        self.max_target_length = config['target_max_seq_length']

        # parameters initialization
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.normal_(self.vocab_linear.weight, std=0.02)
        nn.init.constant_(self.vocab_linear.bias, 0.)

    def generate(self, eval_dataloader):
        generate_corpus = []
        idx2token = eval_dataloader.target_idx2token

        with torch.no_grad():
            for batch_data in eval_dataloader:
                source_text = batch_data['source_idx']
                source_embeddings = self.source_token_embedder(source_text) + \
                                    self.position_embedder(source_text).to(self.device)
                source_padding_mask = torch.eq(source_text, self.padding_token_idx).to(self.device)
                encoder_outputs = self.encoder(source_embeddings,
                                               self_padding_mask=source_padding_mask,
                                               output_all_encoded_layers=False)

                for bid in range(source_text.size(0)):
                    generate_tokens = []
                    prev_token_ids = [self.sos_token_idx]
                    for gen_idx in range(self.max_target_length):
                        input_seq = torch.LongTensor([prev_token_ids]).to(self.device)
                        decoder_input = self.target_token_embedder(input_seq) + \
                                        self.position_embedder(input_seq).to(self.device)
                        decoder_outputs = self.decoder(decoder_input,
                                                       external_states=encoder_outputs[bid, :, :].unsqueeze(0),
                                                       external_padding_mask=source_padding_mask[bid, :].unsqueeze(0))

                        token_logits = self.vocab_linear(decoder_outputs[:, -1, :].unsqueeze(1))
                        token_idx = topk_sampling(token_logits)
                        token_idx = token_idx.item()
                        if token_idx == self.eos_token_idx:
                            break
                        else:
                            generate_tokens.append(idx2token[token_idx])
                            prev_token_ids.append(token_idx)
                    generate_corpus.append(generate_tokens)
        return generate_corpus

    def calculate_loss(self, corpus, epoch_idx=0):
        source_text = corpus['source_idx']

        input_text = corpus['target_idx'][:, :-1]
        target_text = corpus['target_idx'][:, 1:]

        source_embeddings = self.source_token_embedder(source_text) + self.position_embedder(source_text).to(
            self.device)
        source_padding_mask = torch.eq(source_text, self.padding_token_idx).to(self.device)
        encoder_outputs = self.encoder(source_embeddings,
                                       self_padding_mask=source_padding_mask)

        input_embeddings = self.target_token_embedder(input_text) + self.position_embedder(input_text).to(self.device)
        self_padding_mask = torch.eq(input_text, self.padding_token_idx).to(self.device)
        self_attn_mask = self.self_attn_mask(input_text.size(-1)).bool().to(self.device)
        decoder_outputs = self.decoder(input_embeddings,
                                       self_padding_mask=self_padding_mask,
                                       self_attn_mask=self_attn_mask,
                                       external_states=encoder_outputs,
                                       external_padding_mask=source_padding_mask)

        token_logits = self.vocab_linear(decoder_outputs)
        loss = self.loss(token_logits.view(-1, token_logits.size(-1)), target_text.contiguous().view(-1))
        loss = loss.reshape_as(target_text)

        length = corpus['target_length'] - 1
        loss = loss.sum(dim=1) / length

        return loss.mean()
