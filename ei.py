"""
Ensemble Integration

@author: Jamie Bennett, Yan Chak (Richard) Li
"""

import pandas as pd
import numpy as np
import dill as pickle
from copy import copy
from sklearn.utils._testing import ignore_warnings
from sklearn.exceptions import ConvergenceWarning
from sklearn.model_selection import StratifiedKFold
from joblib import Parallel, delayed
from tensorflow.keras.backend import clear_session
from joblib.externals.loky import set_loky_pickler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.base import clone
import warnings
from utils import scores, set_seed, random_integers, sample, retrieve_X_y, append_modality, metric_threshold_dataframes

warnings.filterwarnings("ignore", category=DeprecationWarning)

def create_base_summary(meta_test_dataframe):
    labels = pd.concat([df["labels"] for df in meta_test_dataframe])
    meta_test_averaged_samples = pd.concat(
        [df.drop(columns=["labels"], level=0).groupby(level=(0, 1), axis=1).mean() for df in meta_test_dataframe])
    meta_test_averaged_samples["labels"] = labels
    return metric_threshold_dataframes(meta_test_averaged_samples)

class MeanAggregation:
    def __init__(self):
        pass

    def fit(self, X, y):
        pass

    def predict_proba(self, X):
        predict_positive = X.mean(axis=1)
        return np.transpose(np.array([1 - predict_positive, predict_positive]))


class MedianAggregation:
    def __init__(self):
        pass

    def fit(self, X, y):
        pass

    def predict_proba(self, X):
        predict_positive = X.median(axis=1)
        return np.transpose(np.array([1 - predict_positive, predict_positive]))


class EnsembleIntegration:
    """
    Algorithms to test a variety of ensemble methods.

    Parameters
    ----------
    base_predictors : dictionary
        Base predictors.
    k_outer : int, optional
        Number of outer folds. Default is 5.
    k_inner : int, optional
        Number of inner folds. Default is 5.
    random_state : int, optional
        Random state for cross-validation. The default is 42.

    Returns
    -------
    predictions_df : Pandas dataframe of shape (n_samples, n_base_predictors)
        Matrix of data intended for training of a meta-algorithm.

    To be done:
        - CES ensemble
        - interpretation
        - best base predictor
        - model building
        - think about the use of calibrated classifier in base and meta
    """

    def __init__(self,
                 base_predictors=None,
                 meta_models=None,
                 k_outer=None,
                 k_inner=None,
                 n_samples=None,
                 sampling_strategy="undersampling",
                 sampling_aggregation="mean",
                 n_jobs=-1,
                 random_state=None,
                 parallel_backend="threading",
                 project_name="project"):

        set_seed(random_state)
        # set_loky_pickler("dill")  # not working. Attempt to get Parallel working with KerasClassifier()

        self.base_predictors = base_predictors
        if meta_models is not None:
            self.meta_models = {"S." + k: v for k, v in meta_models.items()}  # suffix denotes stacking
        self.k_outer = k_outer
        self.k_inner = k_inner
        self.n_samples = n_samples
        self.sampling_strategy = sampling_strategy
        self.sampling_aggregation = sampling_aggregation
        self.n_jobs = n_jobs
        self.random_state = random_state
        self.project_name = project_name
        self.parallel_backend = parallel_backend

        self.trained_meta_models = {}
        self.trained_base_predictors = {}

        if k_outer is not None:
            self.cv_outer = StratifiedKFold(n_splits=self.k_outer, shuffle=True,
                                            random_state=random_integers(n_integers=1)[0])
        if k_inner is not None:
            self.cv_inner = StratifiedKFold(n_splits=self.k_inner, shuffle=True,
                                            random_state=random_integers(n_integers=1)[0])
        if n_samples is not None:
            self.random_numbers_for_samples = random_integers(n_integers=n_samples)

        self.meta_training_data = None
        self.meta_test_data = None
        self.base_summary = None

        self.meta_predictions = None
        self.meta_summary = None

    @ignore_warnings(category=ConvergenceWarning)
    def train_meta(self, meta_models=None, display_metrics=True):

        if meta_models is not None:
            self.meta_models = meta_models
            self.meta_models = {"S." + k: v for k, v in meta_models.items()}  # suffix denotes stacking

        additional_meta_models = {"Mean": MeanAggregation(),
                                  "Median": MedianAggregation()}

        self.meta_models = {**additional_meta_models, **self.meta_models}

        print("\nWorking on meta models")

        y_test_combined = []

        for fold_id in range(self.k_outer):
            _, y_test = retrieve_X_y(labelled_data=self.meta_test_data[fold_id])
            y_test_combined.extend(y_test)

        meta_predictions = {}
        performance_metrics = []

        for model_name, model in self.meta_models.items():

            print("\n{model_name:}...".format(model_name=model_name))

            if model_name[:2] == "S.":  # calibrate stacking classifiers
                model = CalibratedClassifierCV(model, ensemble=True)

            y_pred_combined = []

            for fold_id in range(self.k_outer):

                X_train, y_train = retrieve_X_y(labelled_data=self.meta_training_data[fold_id])
                X_test, _ = retrieve_X_y(labelled_data=self.meta_test_data[fold_id])

                if self.sampling_aggregation == "mean":
                    X_train = X_train.groupby(level=0, axis=1).mean()
                    X_test = X_test.groupby(level=0, axis=1).mean()

                model.fit(X_train, y_train)
                y_pred = model.predict_proba(X_test)[:, 1]
                y_pred_combined.extend(y_pred)

            meta_predictions[model_name] = y_pred_combined
            performance_metrics.append(scores(y_test_combined, y_pred_combined, display=display_metrics))

        meta_predictions["labels"] = y_test_combined

        self.meta_predictions = pd.DataFrame.from_dict(meta_predictions)
        self.meta_summary = metric_threshold_dataframes(self.meta_predictions)

        return self

    @ignore_warnings(category=ConvergenceWarning)
    def train_base(self, X, y, base_predictors=None, modality=None):

        if base_predictors is not None:
            self.base_predictors = base_predictors  # update base predictors

        if modality is not None:
            print(f"\nWorking on {modality} data...")

        if (self.meta_training_data or self.meta_test_data) is None:
            self.meta_training_data = self.train_base_inner(X, y, modality)
            self.meta_test_data = self.train_base_outer(X, y, modality)

        else:
            self.meta_training_data = append_modality(self.meta_training_data, self.train_base_inner(X, y, modality))
            self.meta_test_data = append_modality(self.meta_test_data, self.train_base_outer(X, y, modality))

        self.base_summary = create_base_summary(self.meta_test_data)

        return self

    def train_base_inner(self, X, y, modality):
        """
        Perform a round of (inner) k-fold cross validation on each outer
        training set for generation of training data for the meta-algorithm

        Parameters
        ----------
        X : array of shape (n_samples, n_features)
            Dataset.
        y : array of shape (n_samples,)
            Labels.

        Returns
        -------
        meta_training_data : List of length k_outer containing Pandas dataframes
        of shape (n_outer_training_samples, n_base_predictors * n_samples)
        """

        print("\nTraining base predictors on inner training sets...")

        # dictionaries for meta train/test data for each outer fold
        meta_training_data = []
        # define joblib Parallel function
        parallel = Parallel(n_jobs=self.n_jobs, verbose=10, backend=self.parallel_backend)
        for outer_fold_id, (train_index_outer, test_index_outer) in enumerate(self.cv_outer.split(X, y)):
            print("\nGenerating meta-training data for outer fold {outer_fold_id:}...".format(
                outer_fold_id=outer_fold_id))

            X_train_outer = X[train_index_outer]
            y_train_outer = y[train_index_outer]

            # spawn n_jobs jobs for each sample, inner_fold and model
            output = parallel(delayed(self.train_model_fold_sample)(X=X_train_outer,
                                                                    y=y_train_outer,
                                                                    model_params=model_params,
                                                                    fold_params=inner_fold_params,
                                                                    sample_state=sample_state)
                              for model_params in self.base_predictors.items()
                              for inner_fold_params in enumerate(self.cv_inner.split(X_train_outer, y_train_outer))
                              for sample_state in enumerate(self.random_numbers_for_samples))
            combined_predictions = self.combine_data_inner(output, modality)
            meta_training_data.append(combined_predictions)
        return meta_training_data

    def train_base_outer(self, X, y, modality):
        """
        Train each base predictor on each outer training set

        Parameters
        ----------
        X : array of shape (n_samples, n_features)
            Dataset.
        y : array of shape (n_samples,)
            Labels.

        Returns
        -------

        meta_test_data : List of length k_outer containing Pandas dataframes
        of shape (n_outer_test_samples, n_base_predictors * n_samples)
        """

        # define joblib Parallel function
        parallel = Parallel(n_jobs=self.n_jobs, verbose=10, backend=self.parallel_backend)

        print("\nTraining base predictors on outer training sets...")

        # spawn job for each sample, inner_fold and model
        output = parallel(delayed(self.train_model_fold_sample)(X=X,
                                                                y=y,
                                                                model_params=model_params,
                                                                fold_params=outer_fold_params,
                                                                sample_state=sample_state)
                          for model_params in self.base_predictors.items()
                          for outer_fold_params in enumerate(self.cv_outer.split(X, y))
                          for sample_state in enumerate(self.random_numbers_for_samples))
        meta_test_data = self.combine_data_outer(output, modality)

        self.base_summary = create_base_summary(meta_test_data)

        return meta_test_data

    @ignore_warnings(category=ConvergenceWarning)
    def train_model_fold_sample(self, X, y, model_params, fold_params, sample_state):
        clear_session()
        model_name, model_original = model_params
        model = copy(model_original)
        fold_id, (train_index, test_index) = fold_params
        sample_id, sample_random_state = sample_state

        if str(model.__class__).find("tensorflow") == -1:
            model = CalibratedClassifierCV(model, ensemble=True)  # calibrate classifiers

        if str(model.__class__).find("tensorflow") != -1:  # clear any previous TF sessions (just in case)
            clear_session()

        X_train, X_test = X[train_index], X[test_index]
        y_train, y_test = y[train_index], y[test_index]
        X_sample, y_sample = sample(X_train, y_train, strategy=self.sampling_strategy, random_state=sample_random_state)
        model.fit(X_sample, y_sample)

        if str(model.__class__).find("tensorflow") == -1:
            y_pred = model.predict_proba(X_test)[:, 1]  # assumes other models are sklearn
        else:
            y_pred = np.squeeze(model.predict(X_test))

        metrics = scores(y_test, y_pred)

        results_dict = {"model_name": model_name,
                        "sample_id": sample_id,
                        "fold_id": fold_id,
                        "scores": metrics,
                        "model": model,
                        "y_pred": y_pred,
                        "labels": y_test}

        return results_dict

    def combine_data_inner(self, list_of_dicts, modality):  # we don't save the models trained here
        # dictionary to store predictions
        combined_predictions = {}
        # combine fold predictions for each model
        for model_name in self.base_predictors.keys():
            for sample_id in range(self.n_samples):
                model_predictions = np.concatenate(
                    list(d["y_pred"] for d in list_of_dicts if
                         d["model_name"] == model_name and d["sample_id"] == sample_id))
                combined_predictions[modality, model_name, sample_id] = model_predictions
        labels = np.concatenate(list(d["labels"] for d in list_of_dicts if
                                     d["model_name"] == list(self.base_predictors.keys())[0] and d["sample_id"] == 0))
        combined_predictions = pd.DataFrame(combined_predictions).rename_axis(["modality", "base predictor", "sample"],
                                                                              axis=1)
        combined_predictions["labels"] = labels
        return combined_predictions

    def combine_data_outer(self, list_of_dicts, modality):
        combined_predictions = []
        for fold_id in range(self.k_outer):
            predictions = {}
            for model_name in self.base_predictors.keys():
                for sample_id in range(self.n_samples):
                    model_predictions = list([d["y_pred"], d["model"]] for d in list_of_dicts if
                                             d["fold_id"] == fold_id and d["model_name"] == model_name and d[
                                                 "sample_id"] == sample_id)
                    predictions[modality, model_name, sample_id] = model_predictions[0][0]
                    # self.trained_base_predictors[(model_name, sample_id)] = model_predictions[0][1] # need to write to file to avoid memory issues
            labels = [d["labels"] for d in list_of_dicts if
                      d["fold_id"] == fold_id and d["model_name"] == list(self.base_predictors.keys())[0] and d[
                          "sample_id"] == 0]
            predictions = pd.DataFrame(predictions)
            predictions["labels"] = labels[0]
            combined_predictions.append(predictions.rename_axis(["modality", "base predictor", "sample"], axis=1))
        return combined_predictions

    def save(self, path=None):
        if path is None:
            path = f"EI.{self.project_name}"
        with open(path, 'wb') as f:
            pickle.dump(self, f)
        print(f"\nSaved to {path}\n")

    @classmethod
    def load(cls, filename):
        with open(filename, 'rb') as f:
            return pickle.load(f)
