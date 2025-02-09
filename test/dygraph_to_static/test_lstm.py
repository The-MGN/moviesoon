# copyright (c) 2020 PaddlePaddle Authors. All Rights Reserve.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import tempfile
import unittest

import numpy as np
from dygraph_to_static_utils import (
    Dy2StTestBase,
    test_ast_only,
    test_legacy_and_pt_and_pir,
)

import paddle
from paddle import nn
from paddle.framework import use_pir_api


class LSTMLayer(nn.Layer):
    def __init__(self, in_channels, hidden_size):
        super().__init__()
        self.cell = nn.LSTM(
            in_channels, hidden_size, direction='bidirectional', num_layers=2
        )

    def forward(self, x):
        x, _ = self.cell(x)
        return x


class Net(nn.Layer):
    def __init__(self, in_channels, hidden_size):
        super().__init__()
        self.lstm = LSTMLayer(in_channels, hidden_size)

    def forward(self, x):
        x = self.lstm(x)
        return x


class TestLstm(Dy2StTestBase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_lstm(self, to_static):
        paddle.jit.enable_to_static(to_static)

        paddle.static.default_main_program().random_seed = 1001
        paddle.static.default_startup_program().random_seed = 1001

        net = paddle.jit.to_static(Net(12, 2))
        x = paddle.zeros((2, 10, 12))
        y = net(x)
        return y.numpy()

    def test_lstm_to_static(self):
        dygraph_out = self.run_lstm(to_static=False)
        static_out = self.run_lstm(to_static=True)
        np.testing.assert_allclose(dygraph_out, static_out, rtol=1e-05)

    def save_in_eval(self, with_training: bool):
        paddle.jit.enable_to_static(True)
        net = Net(12, 2)
        x = paddle.randn((2, 10, 12))
        if with_training:
            x.stop_gradient = False
            dygraph_out = net(x)
            loss = paddle.mean(dygraph_out)
            sgd = paddle.optimizer.SGD(
                learning_rate=0.001, parameters=net.parameters()
            )
            loss.backward()
            sgd.step()
        # switch eval mode firstly
        net.eval()
        x = paddle.randn((2, 10, 12))
        net = paddle.jit.to_static(
            net, input_spec=[paddle.static.InputSpec(shape=[-1, 10, 12])]
        )
        # TODO(pir-save-load): Fix this after we support save/load in PIR
        if not use_pir_api():
            model_path = os.path.join(self.temp_dir.name, 'simple_lstm')
            paddle.jit.save(net, model_path)

            dygraph_out = net(x)
            # load saved model
            load_net = paddle.jit.load(model_path)

            static_out = load_net(x)
            np.testing.assert_allclose(
                dygraph_out.numpy(),
                static_out.numpy(),
                rtol=1e-05,
                err_msg=f'dygraph_out is {dygraph_out}\n static_out is \n{static_out}',
            )
            # switch back into train mode.
            net.train()
            train_out = net(x)
            np.testing.assert_allclose(
                dygraph_out.numpy(),
                train_out.numpy(),
                rtol=1e-05,
                err_msg=f'dygraph_out is {dygraph_out}\n static_out is \n{train_out}',
            )

    @test_ast_only
    @test_legacy_and_pt_and_pir
    def test_save_without_training(self):
        self.save_in_eval(with_training=False)

    @test_ast_only
    @test_legacy_and_pt_and_pir
    def test_save_with_training(self):
        self.save_in_eval(with_training=True)


class LinearNet(nn.Layer):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(10, 12)
        self.dropout = nn.Dropout(0.5)

    def forward(self, x):
        y = self.fc(x)
        y = self.dropout(y)
        return y


class TestSaveInEvalMode(Dy2StTestBase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    @test_legacy_and_pt_and_pir
    def test_save_in_eval(self):
        paddle.jit.enable_to_static(True)
        net = paddle.jit.to_static(LinearNet())
        x = paddle.randn((2, 10))
        x.stop_gradient = False
        dygraph_out = net(x)
        loss = paddle.mean(dygraph_out)
        sgd = paddle.optimizer.SGD(
            learning_rate=0.001, parameters=net.parameters()
        )
        loss.backward()
        sgd.step()
        # switch eval mode firstly
        net.eval()
        # save directly
        net = paddle.jit.to_static(
            net, input_spec=[paddle.static.InputSpec(shape=[-1, 10])]
        )

        # TODO(pir-save-load): Fix this after we support save/load in PIR
        if not use_pir_api():
            model_path = os.path.join(self.temp_dir.name, 'linear_net')
            paddle.jit.save(net, model_path)
            # load saved model
            load_net = paddle.jit.load(model_path)

            x = paddle.randn((2, 10))
            eval_out = net(x)

            infer_out = load_net(x)
            np.testing.assert_allclose(
                eval_out.numpy(),
                infer_out.numpy(),
                rtol=1e-05,
                err_msg=f'eval_out is {eval_out}\n infer_out is \n{infer_out}',
            )


class TestEvalAfterSave(Dy2StTestBase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    @test_legacy_and_pt_and_pir
    def test_eval_after_save(self):
        x = paddle.randn((2, 10, 12)).astype('float32')
        net = Net(12, 2)
        x.stop_gradient = False
        dy_out = net(x)
        loss = paddle.mean(dy_out)
        sgd = paddle.optimizer.SGD(
            learning_rate=0.001, parameters=net.parameters()
        )
        loss.backward()
        sgd.step()
        x = paddle.randn((2, 10, 12)).astype('float32')
        dy_out = net(x)

        # TODO(pir-save-load): Fix this after we support save/load in PIR
        if not use_pir_api():
            # save model
            model_path = os.path.join(self.temp_dir.name, 'jit.save/lstm')
            paddle.jit.save(net, model_path, input_spec=[x])
            load_net = paddle.jit.load(model_path)
            load_out = load_net(x)
            np.testing.assert_allclose(
                dy_out.numpy(), load_out.numpy(), rtol=1e-05
            )
        # eval
        net.eval()
        out = net(x)
        np.testing.assert_allclose(dy_out.numpy(), out.numpy(), rtol=1e-05)


if __name__ == "__main__":
    unittest.main()
