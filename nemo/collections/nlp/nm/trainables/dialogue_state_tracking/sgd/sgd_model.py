#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Jan 26 20:56:22 2020

@author: ebakhturina
"""

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from nemo.backends.pytorch.nm import TrainableNM
from nemo.core import ChannelType, EmbeddedTextType, LabelsType, LengthsType, LogitsType, NeuralType
from nemo.utils.decorators import add_port_docs


class Logits(nn.Module):
    def __init__(self, num_classes, embedding_dim):
        """Get logits for elements by conditioning on utterance embedding.

        Args:
          element_embeddings: A tensor of shape (batch_size, num_elements,
            embedding_dim).
          num_classes: An int containing the number of classes for which logits are
            to be generated.

        Returns:
          A tensor of shape (batch_size, num_elements, num_classes) containing the
          logits.
        """
        super().__init__()

        self.num_classes = num_classes
        self.utterance_proj = nn.Linear(embedding_dim, embedding_dim)
        self.activation = F.gelu

        self.layer1 = nn.Linear(2 * embedding_dim, embedding_dim)
        self.layer2 = nn.Linear(embedding_dim, num_classes)

    def forward(self, encoded_utterance, element_embeddings):

        """
        encoded_utterance - [CLS] token hidden state from BERT encoding of the utterance
        
        """
        _, num_elements, _ = element_embeddings.size()

        # Project the utterance embeddings.
        utterance_embedding = self.utterance_proj(encoded_utterance)
        utterance_embedding = self.activation(utterance_embedding)

        # Combine the utterance and element embeddings.
        repeated_utterance_embedding = utterance_embedding.unsqueeze(1).repeat(1, num_elements, 1)

        utterance_element_emb = torch.cat([repeated_utterance_embedding, element_embeddings], axis=2)
        logits = self.layer1(utterance_element_emb)
        logits = self.activation(logits)
        logits = self.layer2(logits)
        return logits


class LogitsNew(nn.Module):
    def __init__(self, num_classes, embedding_dim):
        """Get logits for elements by conditioning on utterance embedding.

        Args:
          element_embeddings: A tensor of shape (batch_size, num_elements,
            embedding_dim).
          num_classes: An int containing the number of classes for which logits are
            to be generated.

        Returns:
          A tensor of shape (batch_size, num_elements, num_classes) containing the
          logits.
        """
        super().__init__()

        self.num_classes = num_classes
        self.utterance_proj = nn.Linear(embedding_dim, embedding_dim)
        self.activation = F.gelu

        #self.layer1 = nn.Linear(2 * embedding_dim, embedding_dim)
        #self.layer2 = nn.Linear(embedding_dim, num_elements*num_classes)

    def forward(self, encoded_utterance, element_embeddings, weight_matrix):
        """
        encoded_utterance - [CLS] token hidden state from BERT encoding of the utterance

        """
        _, num_elements, _ = element_embeddings.size()

        # Project the utterance embeddings.
        utterance_embedding = self.utterance_proj(encoded_utterance)
        utterance_embedding = self.activation(utterance_embedding)

        # Combine the utterance and element embeddings.
        repeated_utterance_embedding = utterance_embedding.unsqueeze(1).repeat(1, num_elements, 1)

        utterance_element_emb = torch.cat([repeated_utterance_embedding, element_embeddings], axis=2)

        utterance_element_emb = utterance_element_emb.unsqueeze(-1).repeat(1, 1, 1, self.num_classes)

        logits = utterance_element_emb * weight_matrix
        logits = logits.sum(dim=-2)
        #logits = self.layer1(utterance_element_emb)
        #logits = self.activation(logits)
        #logits = self.layer2(logits)
        return logits


class SGDModel(TrainableNM):
    """
    TODO
      num_categorical_slot_values,
      num_intents,
    """

    @property
    @add_port_docs()
    def input_ports(self):
        return {
            "encoded_utterance": NeuralType(('B', 'T'), EmbeddedTextType()),
            "token_embeddings": NeuralType(('B', 'T', 'C'), ChannelType()),
            "utterance_mask": NeuralType(('B', 'T'), ChannelType()),
            "num_categorical_slot_values": NeuralType(('B', 'T'), LengthsType()),
            "num_intents": NeuralType(('B'), LengthsType()),
            "req_num_slots": NeuralType(('B'), LengthsType()),
            "service_ids": NeuralType(('B'), ChannelType()),
            # "slot_status_tokens": NeuralType(('B', 'T'), LabelsType()),
        }

    @property
    @add_port_docs()
    def output_ports(self):
        """Returns definitions of module output ports.

        hidden_states:
            0: AxisType(BatchTag)

            1: AxisType(TimeTag)

            2: AxisType(ChannelTag)
        """
        return {
            "logit_intent_status": NeuralType(('B', 'T', 'C'), LogitsType()),
            "logit_req_slot_status": NeuralType(('B', 'T'), LogitsType()),
            "req_slot_mask": NeuralType(('B', 'T'), ChannelType()),
            "logit_cat_slot_status": NeuralType(('B', 'T', 'C'), LogitsType()),
            "logit_cat_slot_value": NeuralType(('B', 'T', 'C'), LogitsType()),
            "logit_noncat_slot_status": NeuralType(('B', 'T', 'C'), LogitsType()),
            "logit_noncat_slot_start": NeuralType(('B', 'T', 'C'), LogitsType()),
            "logit_noncat_slot_end": NeuralType(('B', 'T', 'C'), LogitsType()),
            # "logit_slot_status_tokens": NeuralType(('B', 'T', 'C'), LogitsType()),
        }

    def __init__(self, embedding_dim, schema_emb_processor):
        """Get logits for elements by conditioning on utterance embedding.

        Args:
          element_embeddings: A tensor of shape (batch_size, num_elements,
            embedding_dim).
          num_classes: An int containing the number of classes for which logits are
            to be generated.
          name_scope: The name scope to be used for layers.
    
        Returns:
          A tensor of shape (batch_size, num_elements, num_classes) containing the
          logits.
        """
        super().__init__()

        self.schema_config = schema_emb_processor.schema_config
        self._slots_status_model = schema_emb_processor.schemas._slots_status_model

        # Add a trainable vector for the NONE intent
        self.none_intent_vector = torch.empty((1, 1, embedding_dim), requires_grad=True).to(self._device)
        # TODO truncated norm init
        nn.init.normal_(self.none_intent_vector, std=0.02)
        self.none_intent_vector = torch.nn.Parameter(self.none_intent_vector).to(self._device)

        self.intent_layer = Logits(1, embedding_dim).to(self._device)
        self.requested_slots_layer = Logits(1, embedding_dim).to(self._device)

        self.cat_slot_value_layer = Logits(1, embedding_dim).to(self._device)

        # dim 2 for non_categorical slot - to represent start and end position
        self.noncat_layer1 = nn.Linear(2 * embedding_dim, embedding_dim).to(self._device)
        self.noncat_activation = F.gelu
        self.noncat_layer2 = nn.Linear(embedding_dim, 2).to(self._device)

        # slot_status_token layers
        if self._slots_status_model == "special_tokens_multi":
            self.slot_status_token_layer1 = nn.Linear(2 * embedding_dim, embedding_dim).to(self._device)
            self.slot_status_token_activation = F.gelu
            self.slot_status_token_layer2 = nn.Linear(embedding_dim, 3).to(self._device)
        elif self._slots_status_model in ["cls_token", "special_tokens_single", "special_tokens_double"]:
            # Slot status values: none, dontcare, active.
            self.cat_slot_status_layer = LogitsNew(3, embedding_dim).to(self._device)
            self.noncat_slot_status_layer = Logits(3, embedding_dim).to(self._device)
            # # Slot status values: none, dontcare, active.
            # self.cat_slot_status_layer = Logits(3, embedding_dim, self.schema_config["MAX_NUM_CAT_SLOT"]).to(self._device)
            # self.noncat_slot_status_layer = Logits(3, embedding_dim, self.schema_config["MAX_NUM_NONCAT_SLOT"]).to(self._device)

        num_services = len(schema_emb_processor.schemas.services)
        self.intents_emb = nn.Embedding(num_services, self.schema_config["MAX_NUM_INTENT"] * embedding_dim)
        self.cat_slot_emb = nn.Embedding(num_services, self.schema_config["MAX_NUM_CAT_SLOT"] * embedding_dim)
        self.cat_slot_value_emb = nn.Embedding(
            num_services,
            self.schema_config["MAX_NUM_CAT_SLOT"] * self.schema_config["MAX_NUM_VALUE_PER_CAT_SLOT"] * embedding_dim,
        )
        self.noncat_slot_emb = nn.Embedding(num_services, self.schema_config["MAX_NUM_NONCAT_SLOT"] * embedding_dim)
        self.req_slot_emb = nn.Embedding(
            num_services,
            (self.schema_config["MAX_NUM_CAT_SLOT"] + self.schema_config["MAX_NUM_NONCAT_SLOT"]) * embedding_dim,
        )



        #change here
        self.noncat_slot_emb_weights = nn.Embedding(num_services, self.schema_config["MAX_NUM_NONCAT_SLOT"] * embedding_dim * 2 * 3)
        #torch.empty((1, self.schema_config["MAX_NUM_NONCAT_SLOT"], 2*embedding_dim, 3), requires_grad=True)
        nn.init.uniform_(self.noncat_slot_emb_weights.weight, -0.02, 0.02)
        #self.weight_matrix = torch.nn.Parameter(weight_matrix)
        self.cat_slot_emb_weights = nn.Embedding(num_services, self.schema_config["MAX_NUM_CAT_SLOT"] * embedding_dim * 2 * 3)
        #torch.empty((1, self.schema_config["MAX_NUM_NONCAT_SLOT"], 2*embedding_dim, 3), requires_grad=True)
        nn.init.uniform_(self.cat_slot_emb_weights.weight, -0.02, 0.02)
        #self.weight_matrix = torch.nn.Parameter(weight_matrix)



        # initialize schema embeddings from the BERT generated embeddings
        schema_embeddings = schema_emb_processor.get_schema_embeddings()
        self.intents_emb.weight.data.copy_(
            torch.from_numpy(np.stack(schema_embeddings['intent_emb']).reshape(num_services, -1))
        )
        self.cat_slot_emb.weight.data.copy_(
            torch.from_numpy(np.stack(schema_embeddings['cat_slot_emb']).reshape(num_services, -1))
        )
        self.cat_slot_value_emb.weight.data.copy_(
            torch.from_numpy(np.stack(schema_embeddings['cat_slot_value_emb']).reshape(num_services, -1))
        )
        self.noncat_slot_emb.weight.data.copy_(
            torch.from_numpy(np.stack(schema_embeddings['noncat_slot_emb']).reshape(num_services, -1))
        )
        self.req_slot_emb.weight.data.copy_(
            torch.from_numpy(np.stack(schema_embeddings['req_slot_emb']).reshape(num_services, -1))
        )

        if not schema_emb_processor.is_trainable:
            self.intents_emb.weight.requires_grad = False
            self.cat_slot_emb.weight.requires_grad = False
            self.cat_slot_value_emb.weight.requires_grad = False
            self.noncat_slot_emb.weight.requires_grad = False
            self.req_slot_emb.weight.requires_grad = False

        self.to(self._device)

    def forward(
        self,
        encoded_utterance,
        token_embeddings,
        utterance_mask,
        num_categorical_slot_values,
        num_intents,
        req_num_slots,
        service_ids,
        # slot_status_tokens,
    ):
        """
        encoded_utterance - [CLS] token hidden state from BERT encoding of the utterance
        
        """
        batch_size, emb_dim = encoded_utterance.size()
        intent_embeddings = self.intents_emb(service_ids).view(batch_size, -1, emb_dim)
        cat_slot_emb = self.cat_slot_emb(service_ids).view(batch_size, -1, emb_dim)
        max_number_cat_slots = cat_slot_emb.shape[1]
        cat_slot_value_emb = self.cat_slot_value_emb(service_ids).view(batch_size, max_number_cat_slots, -1, emb_dim)
        noncat_slot_emb = self.noncat_slot_emb(service_ids).view(batch_size, -1, emb_dim)
        req_slot_emb = self.req_slot_emb(service_ids).view(batch_size, -1, emb_dim)

        noncat_slot_emb_weights = self.noncat_slot_emb_weights(service_ids).view(batch_size, -1, 2*emb_dim, 3)
        cat_slot_emb_weights = self.cat_slot_emb_weights(service_ids).view(batch_size, -1, 2*emb_dim, 3)

        logit_intent_status = self._get_intents(encoded_utterance, intent_embeddings, num_intents)

        logit_req_slot_status, req_slot_mask = self._get_requested_slots(
            encoded_utterance, req_slot_emb, req_num_slots
        )

        logit_cat_slot_value = self._get_categorical_slot_values(
            encoded_utterance, cat_slot_emb, cat_slot_value_emb, num_categorical_slot_values
        )

        logit_noncat_slot_start, logit_noncat_slot_end = self._get_noncategorical_slot_values(
            encoded_utterance, utterance_mask, noncat_slot_emb, token_embeddings
        )

        logit_cat_slot_status, logit_noncat_slot_status = self._get_slots_status(
            cat_slot_emb, noncat_slot_emb, token_embeddings, encoded_utterance, cat_slot_emb_weights, noncat_slot_emb_weights
        )

        return (
            logit_intent_status,
            logit_req_slot_status,
            req_slot_mask,
            logit_cat_slot_status,
            logit_cat_slot_value,
            logit_noncat_slot_status,
            logit_noncat_slot_start,
            logit_noncat_slot_end,
        )

    def _get_intents(self, encoded_utterance, intent_embeddings, num_intents):
        """
        Args:
            intent_embedding - BERT schema embeddings
            num_intents - number of intents associated with a particular service
            encoded_utterance - representation of untterance
        """
        batch_size, max_num_intents, _ = intent_embeddings.size()

        # Add a trainable vector for the NONE intent.
        repeated_none_intent_vector = self.none_intent_vector.repeat(batch_size, 1, 1)
        intent_embeddings = torch.cat([repeated_none_intent_vector, intent_embeddings], axis=1)
        logits = self.intent_layer(encoded_utterance, intent_embeddings)
        logits = logits.squeeze(axis=-1)  # Shape: (batch_size, max_intents + 1)

        # Mask out logits for padded intents, 1 is added to account for NONE intent.
        mask, negative_logits = self._get_mask(logits, max_num_intents + 1, num_intents + 1)
        return torch.where(mask, logits, negative_logits)

    def _get_requested_slots(self, encoded_utterance, requested_slot_emb, req_num_slots):
        """Obtain logits for requested slots."""

        logits = self.requested_slots_layer(encoded_utterance, requested_slot_emb)
        logits = logits.squeeze(axis=-1)

        # logits shape: (batch_size, max_num_slots)
        max_num_requested_slots = logits.size()[-1]
        req_slot_mask, _ = self._get_mask(logits, max_num_requested_slots, req_num_slots)
        return logits, req_slot_mask.view(-1)

    def _get_categorical_slot_values(
        self, encoded_utterance, cat_slot_emb, cat_slot_value_emb, num_categorical_slot_values
    ):
        """
        Obtain logits for status and values for categorical slots
        Slot status values: none, dontcare, active
        """

        # Predict the goal value.
        # Shape: (batch_size, max_categorical_slots, max_categorical_values, embedding_dim).
        _, max_num_slots, max_num_values, embedding_dim = cat_slot_value_emb.size()
        cat_slot_value_emb_reshaped = cat_slot_value_emb.view(-1, max_num_slots * max_num_values, embedding_dim)

        value_logits = self.cat_slot_value_layer(encoded_utterance, cat_slot_value_emb_reshaped)

        # Reshape to obtain the logits for all slots.
        value_logits = value_logits.view(-1, max_num_slots, max_num_values)

        # Mask out logits for padded slots and values because they will be softmaxed
        cat_slot_values_mask, negative_logits = self._get_mask(
            value_logits, max_num_values, num_categorical_slot_values
        )

        value_logits = torch.where(cat_slot_values_mask, value_logits, negative_logits)
        return value_logits

    def _get_noncategorical_slot_values(self, encoded_utterance, utterance_mask, noncat_slot_emb, token_embeddings):
        """
        Obtain logits for status and slot spans for non-categorical slots.
        Slot status values: none, dontcare, active
        """

        # Predict the distribution for span indices.
        max_num_tokens = token_embeddings.size()[1]
        max_num_slots = noncat_slot_emb.size()[1]

        repeated_token_embeddings = token_embeddings.unsqueeze(1).repeat(1, max_num_slots, 1, 1)
        repeated_slot_embeddings = noncat_slot_emb.unsqueeze(2).repeat(1, 1, max_num_tokens, 1)

        # Shape: (batch_size, max_num_slots, max_num_tokens, 2 * embedding_dim).
        slot_token_embeddings = torch.cat([repeated_slot_embeddings, repeated_token_embeddings], axis=3)

        # Project the combined embeddings to obtain logits, Shape: (batch_size, max_num_slots, max_num_tokens, 2)
        span_logits = self.noncat_layer1(slot_token_embeddings)
        span_logits = self.noncat_activation(span_logits)
        span_logits = self.noncat_layer2(span_logits)

        # Mask out invalid logits for padded tokens.
        utterance_mask = utterance_mask.to(bool)  # Shape: (batch_size, max_num_tokens).
        repeated_utterance_mask = utterance_mask.unsqueeze(1).unsqueeze(3).repeat(1, max_num_slots, 1, 2)
        negative_logits = (torch.finfo(span_logits.dtype).max * -0.7) * torch.ones(
            span_logits.size(), device=self._device, dtype=span_logits.dtype
        )

        span_logits = torch.where(repeated_utterance_mask, span_logits, negative_logits)

        # Shape of both tensors: (batch_size, max_num_slots, max_num_tokens).
        span_start_logits, span_end_logits = torch.unbind(span_logits, dim=3)
        return span_start_logits, span_end_logits

    def _get_slots_status(self, cat_slot_emb, noncat_slot_emb, token_embeddings, encoded_utterance, cat_slot_emb_weights, noncat_slot_emb_weights):
        if self._slots_status_model == "cls_token":
            # Predict the status of all categorical slots.
            logit_cat_slot_status = self.cat_slot_status_layer(encoded_utterance, cat_slot_emb, cat_slot_emb_weights)
            # Predict the status of all non-categorical slots.
            #logit_noncat_slot_status = self.noncat_slot_status_layer(encoded_utterance, noncat_slot_emb, noncat_slot_emb_weights)
            logit_noncat_slot_status = self.noncat_slot_status_layer(encoded_utterance, noncat_slot_emb)

            # # Predict the status of all categorical slots.
            # logit_cat_slot_status = self.cat_slot_status_layer(encoded_utterance, cat_slot_emb)
            # # Predict the status of all non-categorical slots.
            # logit_noncat_slot_status = self.noncat_slot_status_layer(encoded_utterance, noncat_slot_emb)
        elif self._slots_status_model == "special_tokens_single":
            logit_cat_slot_status = self.cat_slot_status_layer(token_embeddings[:, -1], cat_slot_emb)
            #logit_noncat_slot_status = self.noncat_slot_status_layer(token_embeddings[:, -1], noncat_slot_emb)
            logit_noncat_slot_status = self.cat_slot_status_layer(token_embeddings[:, -1], noncat_slot_emb)
        elif self._slots_status_model == "special_tokens_double":
            logit_cat_slot_status = self.cat_slot_status_layer(token_embeddings[:, -2], cat_slot_emb)
            logit_noncat_slot_status = self.noncat_slot_status_layer(token_embeddings[:, -1], noncat_slot_emb)
        elif self._slots_status_model == "special_tokens_multi":
            token_embeddings_status = token_embeddings[
                :, -(self.schema_config["MAX_NUM_CAT_SLOT"] + self.schema_config["MAX_NUM_NONCAT_SLOT"]) :
            ]
            all_slot_emb = torch.cat([cat_slot_emb, noncat_slot_emb], axis=1)
            slot_token_embeddings = torch.cat([token_embeddings_status, all_slot_emb], axis=-1)

            # Project the combined embeddings to obtain logits, Shape: (batch_size, max_num_slots, max_num_tokens, 2)
            logit_slot_status_tokens = self.slot_status_token_layer1(slot_token_embeddings)
            logit_slot_status_tokens = self.slot_status_token_activation(logit_slot_status_tokens)
            logit_slot_status_tokens = self.slot_status_token_layer2(logit_slot_status_tokens)

            # noncat_status_logits = self.slot_status_token_layer1(slot_noncat_token_embeddings)
            # noncat_status_logits = self.slot_status_token_activation(noncat_status_logits)
            # noncat_status_logits = self.slot_status_token_layer2(noncat_status_logits)

            logit_cat_slot_status = logit_slot_status_tokens[:, : self.schema_config["MAX_NUM_CAT_SLOT"]]
            logit_noncat_slot_status = logit_slot_status_tokens[:, self.schema_config["MAX_NUM_CAT_SLOT"] :]

        return logit_cat_slot_status, logit_noncat_slot_status

    def _get_mask(self, logits, max_length, actual_length):
        mask = torch.arange(0, max_length, 1, device=self._device) < torch.unsqueeze(actual_length, dim=-1)
        negative_logits = (torch.finfo(logits.dtype).max * -0.7) * torch.ones(
            logits.size(), device=self._device, dtype=logits.dtype
        )
        return mask, negative_logits
