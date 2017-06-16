import os
import shutil
import sys
import tempfile
from argparse import Namespace
from io import StringIO
from itertools import islice
from unittest import TestCase

import numpy as np
import pandas as pd
from keras.callbacks import History
from numpy.testing import assert_array_equal

from bisemantic.console import main, data_file, fix_columns, TrainingHistory
from bisemantic.main import TextualEquivalenceModel
from bisemantic.data import cross_validation_partitions, UniformLengthEmbeddingGenerator


class TestPreprocess(TestCase):
    def setUp(self):
        self.train = data_file("test/resources/train.csv")

    def test_load_training_data(self):
        self.assertIsInstance(self.train, pd.DataFrame)
        assert_array_equal(["text1", "text2", "label"], self.train.columns)
        self.assertEqual(100, len(self.train))

    def test_load_test_data(self):
        actual = data_file("test/resources/test.csv")
        self.assertIsInstance(actual, pd.DataFrame)
        assert_array_equal(["text1", "text2"], actual.columns)
        self.assertEqual(9, len(actual))

    def test_load_data_with_null(self):
        actual = data_file("test/resources/data_with_null_values.csv")
        self.assertIsInstance(actual, pd.DataFrame)
        assert_array_equal(["text1", "text2", "label"], actual.columns)
        self.assertEqual(3, len(actual))

    def test_fix_columns_with_no_rename(self):
        train = fix_columns(self.train, Namespace(text_1_name=None, text_2_name=None, label_name=None))
        assert_array_equal(["text1", "text2", "label"], train.columns)

    def test_fix_columns_with_invalid_column_name(self):
        self.assertRaises(ValueError, fix_columns, self.train,
                          Namespace(text_1_name="bogus", text_2_name=None, label_name=None))

    def test_cross_validate(self):
        k = 3
        splits = cross_validation_partitions(self.train, 0.8, k)
        self.assertEqual(k, len(splits))
        for i in range(k):
            s = splits[i]
            self.assertIsInstance(s[0], pd.DataFrame)
            self.assertIsInstance(s[1], pd.DataFrame)
            self.assertEqual(80, len(s[0]))
            self.assertEqual(20, len(s[1]))


# "small data" means data size < block size, "big data" means data size > block size.
class TestDataGenerator(TestCase):
    def setUp(self):
        self.train = data_file("test/resources/train.csv")
        self.test = data_file("test/resources/test.csv")

    def test_small_labeled_data(self):
        g = UniformLengthEmbeddingGenerator(self.train)
        # Get two epochs' worth of data.
        batches = list(islice(g(), 8))
        self._validate_labeled_epochs(batches, [32, 32, 32, 4], 40)

    def test_small_labeled_data_specified_maximum_tokens(self):
        g = UniformLengthEmbeddingGenerator(self.train, maximum_tokens=20)
        # Get two epochs' worth of data.
        batches = list(islice(g(), 8))
        self._validate_labeled_epochs(batches, [32, 32, 32, 4], 20)

    def test_small_unlabeled_data(self):
        g = UniformLengthEmbeddingGenerator(self.test)
        # Get two epochs' worth of data.
        batches = list(islice(g(), 8))
        self._validate_unlabeled_epochs(batches, [9], 20)

    def test_big_labeled_data(self):
        g = UniformLengthEmbeddingGenerator(self.train, block_size=50)
        # Get two epochs' worth of data.
        batches = list(islice(g(), 8))
        self._validate_labeled_epochs(batches, [32, 18, 32, 18], 40)

    def test_embed_function(self):
        g, batches_per_epoch = UniformLengthEmbeddingGenerator.embed(self.train)
        self.assertEqual(4, batches_per_epoch)
        # Get two epochs' worth of data.
        batches = list(islice(g, 8))
        self._validate_labeled_epochs(batches, [32, 32, 32, 4], 40)

    def _validate_labeled_epochs(self, batches, expected_batch_sizes, maximum_tokens):
        for i in range(len(expected_batch_sizes)):
            expected_batch_size = expected_batch_sizes[i]
            # Did we get the data we expected?
            self.assertIsInstance(batches[i], tuple)
            self.assertEqual(2, len(batches[i]))
            embeddings, labels = batches[i]
            self.assertEqual((expected_batch_size,), labels.shape)
            self.assertIsInstance(embeddings, list)
            self.assertEqual(2, len(embeddings))
            embedding_1, embedding_2 = embeddings
            self.assertEqual((expected_batch_size, maximum_tokens, 300), embedding_1.shape)
            self.assertEqual((expected_batch_size, maximum_tokens, 300), embedding_2.shape)
            self.assertEqual((expected_batch_size,), labels.shape)
            # Does it repeat after we start the epoch over?
            (embedding_1_a, embedding_2_a), labels_a = batches[i + 4]
            assert_array_equal(embedding_1, embedding_1_a)
            assert_array_equal(embedding_2, embedding_2_a)
            assert_array_equal(labels, labels_a)

    def _validate_unlabeled_epochs(self, batches, expected_batch_sizes, maximum_tokens):
        for i in range(len(expected_batch_sizes)):
            expected_batch_size = expected_batch_sizes[i]
            # Did we get the data we expected?
            self.assertIsInstance(batches[i], list)
            self.assertEqual(2, len(batches[i]))
            embedding_1, embedding_2 = batches[i]
            self.assertEqual((expected_batch_size, maximum_tokens, 300), embedding_1.shape)
            self.assertEqual((expected_batch_size, maximum_tokens, 300), embedding_2.shape)
            # Does it repeat after we start the epoch over?
            embedding_1_a, embedding_2_a = batches[i + 4]
            assert_array_equal(embedding_1, embedding_1_a)
            assert_array_equal(embedding_2, embedding_2_a)


class TestModel(TestCase):
    def setUp(self):
        data = data_file("test/resources/train.csv")
        n = int(0.8 * len(data))
        self.train = data[:n]
        self.validate = data[n:]
        self.test = data_file("test/resources/test.csv")

    def test_properties(self):
        model = TextualEquivalenceModel.create(40, 300, 128, 0.5)
        self.assertEqual(40, model.maximum_tokens)
        self.assertEqual(300, model.embedding_size)
        self.assertEqual(128, model.lstm_units)
        self.assertEqual(0.5, model.dropout)
        self.assertEqual({"maximum_tokens": 40, "embedding_size": 300, "lstm_units": 128, "dropout": 0.5},
                         model.parameters())

    def test_stringification(self):
        model = TextualEquivalenceModel.create(40, 300, 128, 0.5)
        self.assertEqual(
            "TextualEquivalenceModel(LSTM units = 128, maximum tokens = 40, embedding size = 300, dropout = 0.50)",
            str(model))
        model = TextualEquivalenceModel.create(40, 300, 128, None)
        self.assertEqual(
            "TextualEquivalenceModel(LSTM units = 128, maximum tokens = 40, embedding size = 300, No dropout)",
            str(model))

    def test_train_and_predict(self):
        model, history = TextualEquivalenceModel.train(self.train, 128, 2,
                                                       dropout=0.5, clip_tokens=30,
                                                       validation_data=self.validate, model_directory=None)
        self.assertIsInstance(model, TextualEquivalenceModel)
        self.assertIsInstance(history, History)
        self.assertEqual(
            "TextualEquivalenceModel(LSTM units = 128, maximum tokens = 30, embedding size = 300, dropout = 0.50)",
            str(model))
        predictions = model.predict(self.test)
        self.assertEqual((len(self.test),), predictions.shape)
        self.assertTrue(set(np.unique(predictions)).issubset({0, 1}))


class TestSerialization(TestCase):
    def setUp(self):
        _, self.filename = tempfile.mkstemp('.h5')

    def test_serialization(self):
        model = TextualEquivalenceModel.create(40, 300, 128, 0.5)
        model.save(self.filename)
        deserialized_model = TextualEquivalenceModel.load(self.filename)
        self.assertIsInstance(deserialized_model, TextualEquivalenceModel)
        self.assertEqual(model.maximum_tokens, deserialized_model.maximum_tokens)
        self.assertEqual(model.embedding_size, deserialized_model.embedding_size)
        self.assertEqual(model.lstm_units, deserialized_model.lstm_units)

    def tearDown(self):
        os.remove(self.filename)


class TestCommandLine(TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.mkdtemp()
        self.model_directory = os.path.join(self.temporary_directory, "model")

    def test_no_arguments(self):
        actual = main_function_output([])
        self.assertEqual(
            "usage: bisemantic [-h] [--version] [--log LOG]\n                  {train,continue,predict,cross-validation} ...\n",
            actual)

    def test_version(self):
        actual = main_function_output(["--version"])
        self.assertEqual("""bisemantic 1.0.0\n""", actual)

    def test_cross_validation(self):
        main_function_output(["cross-validation", "test/resources/train.csv",
                              "0.8", "3",
                              "--prefix", "partition",
                              "--output-directory", self.temporary_directory])
        for i in range(1, 3):
            for partition_name in ["train", "validate"]:
                filename = os.path.join(self.temporary_directory, "partition.%d.%s.csv" % (i, partition_name))
                self.assertTrue(os.path.isfile(filename), "%s is not a file" % filename)

    def test_train_predict(self):
        main_function_output(["train", "test/resources/train.csv",
                              "--validation-set", "test/resources/train.csv",
                              "--units", "64",
                              "--dropout", "0.5",
                              "--epochs", "2",
                              "--model", self.model_directory])
        self.assertTrue(os.path.isfile(os.path.join(self.model_directory, "model.h5")))
        training_history_filename = os.path.join(self.model_directory, "training-history.json")
        self.assertTrue(os.path.isfile(training_history_filename))
        training_history = TrainingHistory.load(training_history_filename)
        self.assertEqual("Training history, 1 runs", str(training_history))
        self.assertTrue(os.path.isfile(os.path.join(self.model_directory, "model.info.txt")))
        main_function_output(["predict", self.model_directory, "test/resources/test.csv"])

    def test_train_predict_crossvalidation_fraction_with_continue(self):
        # Train a model.
        main_function_output(["train", "test/resources/train.csv",
                              "--validation-fraction", "0.2",
                              "--units", "64",
                              "--dropout", "0.5",
                              "--epochs", "2",
                              "--model", self.model_directory])
        self.assertTrue(os.path.isfile(os.path.join(self.model_directory, "model.h5")))
        training_history_filename = os.path.join(self.model_directory, "training-history.json")
        self.assertTrue(os.path.isfile(training_history_filename))
        training_history = TrainingHistory.load(training_history_filename)
        self.assertEqual("Training history, 1 runs", str(training_history))
        self.assertTrue(os.path.isfile(os.path.join(self.model_directory, "model.info.txt")))
        # Use it to make predictions on a test set.
        main_function_output(["predict", self.model_directory, "test/resources/test.csv"])
        # Train the model some more.
        main_function_output(["continue",
                              "test/resources/train.csv",
                              self.model_directory,
                              "--validation-fraction", "0.2",
                              "--epochs", "2"])
        training_history = TrainingHistory.load(training_history_filename)
        self.assertEqual("Training history, 2 runs", str(training_history))

    def tearDown(self):
        shutil.rmtree(self.temporary_directory)


def main_function_output(args):
    sys.argv = ["bisemantic"] + args
    sys.stdout = s = StringIO()
    try:
        main()
    except SystemExit:
        pass
    sys.stdout = sys.__stdout__
    return s.getvalue()
