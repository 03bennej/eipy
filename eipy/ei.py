"""
Ensemble Integration

@author: Jamie Bennett, Yan Chak (Richard) Li
"""
import pandas as pd
import numpy as np
import pickle
import copy
from tqdm import tqdm
from sklearn.utils._testing import ignore_warnings
from sklearn.exceptions import ConvergenceWarning
from sklearn.model_selection import StratifiedKFold
from sklearn.base import clone
from joblib import Parallel, delayed
import warnings
from sklearn.pipeline import Pipeline
from eipy.utils import (
    scores,
    set_seed,
    random_integers,
    sample,
    retrieve_X_y,
    append_modality,
    metric_threshold_dataframes,
    create_base_summary,
    safe_predict_proba,
    dummy_cv,
    bar_format
)

warnings.filterwarnings("ignore", category=DeprecationWarning)


class EnsembleIntegration:
    """
    Ensemble Integration.

    Train and test a variety of ensemble classification algorithms using a nested cross
    validation approach.

    Parameters
    ----------
    base_predictors : dict, default=None
        Dictionary of (sklearn-like) base predictors. Can also be passed in the
        train_base method.
    meta_predictors : dict, default=None
        Dictionary of (sklearn-like) stacking algorithms. Can also be passed in the
        train_meta method.
    k_outer : int, default=5
        Number of outer folds.
    k_inner : int, default=5
        Number of inner folds.
    n_samples : int, default=1
        The number of samples to take when balancing classes. Ignored if
        sampling_strategy is None.
    sampling_strategy : str, default=None
        The sampling method for class balancing. Can be set to 'undersampling',
        'oversampling', 'hybrid'.
    sampling_aggregation : str, default='mean'
        Method for combining multiple samples. Only used when n_samples>1. Can be
        'mean' or None.
    n_jobs : int, default=1
        Number of workers for parallelization in joblib.
    random_state : int, default=None
        Random state for cross-validation and use in some models.
    parallel_backend : str, default='loky'
        Backend to use in joblib. See joblib.Parallel() for other options.
    project_name : str, default='project'
        Name of project.
    calibration_model : sklearn estimator, default=None
        Calibrate base predictor predictions with calibration_model. Intended for use
        with sklearn's CalibratedClassifierCV().
    model_building : bool, default=False
        Whether or not to train and save final models.
    verbose : int, default=1
        Verbosity level. Can be set to 0 or 1.

    Attributes
    ----------
    base_summary : dict
        Summary of performance scores for each base predictor. Scores can be accessed
        using the 'metrics' key and corresponding thresholds (if applicable) can be
        accessed in the 'thresholds' key.
    meta_summary : dict
        Summary of performance scores for each ensemble method. Scores can be accessed
        using the 'metrics' key and corresponding thresholds (if applicable) can be
        accessed in the 'thresholds' key.
    meta_training_data : list of pandas.DataFrame
        Training data for ensemble methods, for each outer fold.
        len(meta_training_data) = len(k_outer)
    meta_test_data : list of pandas.DataFrame
        Test data for ensemble methods, for each outer fold. len(meta_test_data) = len(k_outer)
    meta_predictions : pandas.DataFrame
        Combined predictions (across all outer folds) made by each ensemble method.
    modality_names : list of str
        List of modalities in the order in which they were passed to EnsembleIntegration.
    n_features_per_modality : list of int
        List of number of features in each modality corresponding to modality_names.
    random_numbers_for_samples : list of int
        Random numbers used to sample each training fold.
    final_models : dict
        Dictionary of the form {"base models": {}, "meta models": {}}.
        Populated if model_building=True.
    meta_training_data_final: list of pandas.DataFrame
        List containing single dataframe of training data. Final models are
        trained on all available data.
    cv_outer : StratifiedKFold
        StratifiedKFold() cross validator from sklearn.
    cv_inner : StratifiedKFold
        StratifiedKFold() cross validator from sklearn.
    """

    def __init__(
        self,
        base_predictors=None,
        meta_predictors=None,
        k_outer=5,
        k_inner=5,
        n_samples=1,
        sampling_strategy="undersampling",
        sampling_aggregation="mean",
        n_jobs=1,
        random_state=None,
        parallel_backend="loky",
        project_name="project",
        calibration_model=None,
        model_building=False,
        verbose=1,
    ):
        set_seed(random_state)

        self.base_predictors = base_predictors
        self.meta_predictors = meta_predictors
        self.k_outer = k_outer
        self.k_inner = k_inner
        self.n_samples = n_samples
        self.sampling_strategy = sampling_strategy
        self.sampling_aggregation = sampling_aggregation
        self.n_jobs = n_jobs
        self.random_state = random_state
        self.parallel_backend = parallel_backend
        self.project_name = project_name
        self.calibration_model = calibration_model
        self.model_building = model_building
        self.verbose = verbose

        self.final_models = {"base models": {}, "meta models": {}}  # for final model
        self.meta_training_data_final = None  # for final model

        self.cv_outer = StratifiedKFold(
            n_splits=self.k_outer, shuffle=True, random_state=self.random_state
        )

        self.cv_inner = StratifiedKFold(
            n_splits=self.k_inner, shuffle=True, random_state=self.random_state
        )

        self.meta_training_data = None
        self.meta_test_data = None
        self.base_summary = None

        self.meta_predictions = None
        self.meta_summary = None

        self.modality_names = []
        self.n_features_per_modality = []

        self.random_numbers_for_samples = random_integers(
            n_integers=n_samples, seed=self.random_state
        )

    @ignore_warnings(category=ConvergenceWarning)
    def train_base(self, X, y, base_predictors=None, modality=None):
        """
        Train base predictors and generate meta train/test data.

        Parameters
        ----------
        X : array of shape (n_samples, n_features)
            Training vector, where n_samples is the number of samples and
            n_features is the number of features.
        y : array of shape (n_samples,)
            Target vector relative to X.

        Returns
        -------
        self
            Meta train/test data and fitted final base predictors.

        """

        print(f"Training base predictors on {modality}...\n\n... for ensemble performance analysis...")

        self.modality_names.append(modality)
        self.n_features_per_modality.append(X.shape[1])

        if base_predictors is not None:
            self.base_predictors = base_predictors  # update base predictors

        for _, v in self.base_predictors.items():
            if type(v) == Pipeline:
                est_ = list(v.named_steps)[-1]
                if hasattr(v[est_], "random_state") and hasattr(v[est_], "set_params"):
                    v.set_params(**{"{}__random_state".format(est_): self.random_state})
            if hasattr(v, "random_state") and hasattr(v, "set_params"):
                v.set_params(**{"random_state": self.random_state})

        meta_training_data_modality = self._train_base_inner(
            X=X,
            y=y,
            cv_outer=self.cv_outer,
            cv_inner=self.cv_inner,
            base_predictors=self.base_predictors,
            modality=modality,
        )

        self.meta_training_data = append_modality(
            self.meta_training_data, meta_training_data_modality
        )

        meta_test_data_modality = self._train_base_outer(
            X=X,
            y=y,
            cv_outer=self.cv_outer,
            base_predictors=self.base_predictors,
            modality=modality,
        )

        self.meta_test_data = append_modality(
            self.meta_test_data, meta_test_data_modality
        )  # append data to dataframe

        # create a summary of base predictor performance
        self.base_summary = create_base_summary(self.meta_test_data)

        if self.model_building:
            self._train_base_final(X=X, y=y, modality=modality)

        print("\n")

        return self

    @ignore_warnings(category=ConvergenceWarning)
    def train_meta(self, meta_predictors=None):
        """
        Train meta predictors on data generated by train_base. 

        Parameters
        ----------
        meta_predictors : dict, default=None
            Dictionary of (sklearn-like) stacking algorithms.

        Returns
        -------
        self
            Summary of meta predictor performance and fitted final meta models.
        """

        if meta_predictors is not None:
            self.meta_predictors = meta_predictors

        for _, v in self.meta_predictors.items():
            if type(v) == Pipeline:
                est_ = list(v.named_steps)[-1]
                if hasattr(v[est_], "random_state") and hasattr(v[est_], "set_params"):
                    v.set_params(**{"{}__random_state".format(est_): self.random_state})
            if hasattr(v, "random_state"):
                v.set_params(**{"random_state": self.random_state})

        y_test_combined = []

        for fold_id in range(self.k_outer):
            _, y_test = retrieve_X_y(labelled_data=self.meta_test_data[fold_id])
            y_test_combined.extend(y_test)

        meta_predictions = {}
        performance_metrics = []

        for model_name, model in tqdm(
            self.meta_predictors.items(),
            desc="Analyzing ensembles",
            bar_format=bar_format,
        ):
            y_pred_combined = []

            for fold_id in range(self.k_outer):
                X_train, y_train = retrieve_X_y(
                    labelled_data=self.meta_training_data[fold_id]
                )
                X_test, _ = retrieve_X_y(labelled_data=self.meta_test_data[fold_id])

                if self.sampling_aggregation == "mean":
                    X_train = X_train.groupby(level=[0, 1], axis=1).mean()
                    X_test = X_test.groupby(level=[0, 1], axis=1).mean()

                model.fit(X_train, y_train)
                y_pred = safe_predict_proba(model, X_test)
                y_pred_combined.extend(y_pred)

            meta_predictions[model_name] = y_pred_combined
            performance_metrics.append(
                scores(y_test_combined, y_pred_combined, verbose=0)
            )

        meta_predictions["labels"] = y_test_combined

        self.meta_predictions = pd.DataFrame.from_dict(meta_predictions)
        self.meta_summary = metric_threshold_dataframes(self.meta_predictions)

        if self.model_building:
            for model_name, model in tqdm(
                self.meta_predictors.items(),
                desc="Training final meta models",
                bar_format=bar_format,
            ):
                X_train, y_train = retrieve_X_y(
                    labelled_data=self.meta_training_data_final[0]
                )

                if self.sampling_aggregation == "mean":
                    X_train = X_train.groupby(level=[0, 1], axis=1).mean()
                    X_test = X_test.groupby(level=[0, 1], axis=1).mean()

                model.fit(X_train, y_train)

                self.final_models["meta models"][model_name] = pickle.dumps(model)

        return self

    def predict(self, X_dict, meta_model_key):
        """
        Predict class labels for samples in X

        Parameters
        ----------
        X_dict : dict
            Dictionary of X modalities each having n_samples. Keys and n_features
            must match those seen by train_base.
        meta_model_key :
            The key of the ensemble method selected during performance analysis.

        Returns
        -------
        y_pred : array of shape (n_samples,)
            Vector containing the class labels for each sample.
        """

        meta_prediction_data = None

        for i in range(len(self.modality_names)):
            modality_name = self.modality_names[i]
            n_features = self.n_features_per_modality[i]
            X = X_dict[modality_name]

            # check number of features is the same
            assert X.shape[1] == n_features, (
                f"{X.shape[1]} features were given for {modality_name} modality, but"
                f" {n_features} were used during training."
            )

            base_models = copy.deepcopy(self.final_models["base models"][modality_name])
            for base_model_dict in base_models:
                base_model = pickle.loads(base_model_dict["pickled model"])
                y_pred = safe_predict_proba(base_model, X)

                base_model_dict["fold id"] = 0
                base_model_dict["y_pred"] = y_pred

            combined_predictions = self._combine_predictions_outer(
                base_models, modality_name, model_building=True
            )
            meta_prediction_data = append_modality(
                meta_prediction_data, combined_predictions, model_building=True
            )

        if self.sampling_aggregation == "mean":
            meta_prediction_data = (
                meta_prediction_data[0].groupby(level=[0, 1], axis=1).mean()
            )

        meta_model = pickle.loads(self.final_models["meta models"][meta_model_key])

        y_pred = safe_predict_proba(meta_model, meta_prediction_data)
        return y_pred

    def _train_base_final(self, X, y, modality=None):
        """
        Train final base predictor model.
        """
        print("\n... for final ensemble...")

        meta_training_data_modality = self._train_base_inner(
            X=X,
            y=y,
            cv_inner=self.cv_inner,
            cv_outer=dummy_cv(),  # returns indices of X with an empty set of test indices
            base_predictors=self.base_predictors,
            modality=modality,
        )

        self.meta_training_data_final = append_modality(
            self.meta_training_data_final, meta_training_data_modality
        )

        base_model_list_of_dicts = self._train_base_outer(
            X=X,
            y=y,
            cv_outer=dummy_cv(),  # returns indices of X with an empty set of test indices
            base_predictors=self.base_predictors,
            modality=modality,
            model_building=self.model_building,
        )

        self.final_models["base models"][modality] = base_model_list_of_dicts

    def _train_base_inner(
        self, X, y, cv_outer, cv_inner, base_predictors=None, modality=None
    ):
        """
        Perform a round of (inner) k-fold cross validation on each outer
        training set.
        """

        if base_predictors is not None:
            self.base_predictors = base_predictors  # update base predictors

        # dictionaries for meta train/test data for each outer fold
        meta_training_data_modality = []

        # define joblib Parallel function
        with Parallel(
            n_jobs=self.n_jobs, verbose=0, backend=self.parallel_backend
        ) as parallel:
            for _outer_fold_id, (train_index_outer, _test_index_outer) in enumerate(
                tqdm(
                    cv_outer.split(X, y),
                    total=cv_outer.n_splits,
                    desc="Generating meta training data",
                    bar_format=bar_format,
                )
            ):
                X_train_outer = X[train_index_outer]
                y_train_outer = y[train_index_outer]

                # spawn n_jobs jobs for each sample, inner_fold and model
                output = parallel(
                    delayed(self._train_predict_single_base_predictor)(
                        X=X_train_outer,
                        y=y_train_outer,
                        model_params=model_params,
                        fold_params=inner_fold_params,
                        sample_state=sample_state,
                    )
                    for model_params in self.base_predictors.items()
                    for inner_fold_params in enumerate(
                        cv_inner.split(X_train_outer, y_train_outer)
                    )
                    for sample_state in enumerate(self.random_numbers_for_samples)
                )

                combined_predictions = self._combine_predictions_inner(output, modality)
                meta_training_data_modality.append(combined_predictions)

        return meta_training_data_modality

    def _train_base_outer(
        self, X, y, cv_outer, base_predictors=None, modality=None, model_building=False
    ):
        """
        Train each base predictor on each outer training set.
        """

        if model_building:
            progress_string = "Training final base predictors"
        else:
            progress_string = "Generating meta test data"

        if base_predictors is not None:
            self.base_predictors = base_predictors  # update base predictors

        # define joblib Parallel function
        with Parallel(
            n_jobs=self.n_jobs, verbose=0, backend=self.parallel_backend
        ) as parallel:
            # spawn job for each sample, outer_fold and model
            output = parallel(
                delayed(self._train_predict_single_base_predictor)(
                    X=X,
                    y=y,
                    model_params=model_params,
                    fold_params=outer_fold_params,
                    sample_state=sample_state,
                    model_building=model_building,
                )
                for model_params in tqdm(
                    self.base_predictors.items(),
                    desc=progress_string,
                    bar_format=bar_format,
                )
                for outer_fold_params in enumerate(cv_outer.split(X, y))
                for sample_state in enumerate(self.random_numbers_for_samples)
            )

        if model_building:
            return output
        else:
            return self._combine_predictions_outer(output, modality)

    @ignore_warnings(category=ConvergenceWarning)
    def _train_predict_single_base_predictor(
        self, X, y, model_params, fold_params, sample_state, model_building=False
    ):
        """
        Train/test single base predictor, on a given training fold,
        subject to a given sampling strategy.
        """

        model_name, model = model_params

        model = clone(model)

        fold_id, (train_index, test_index) = fold_params
        sample_id, sample_random_state = sample_state

        X_train, X_test = X[train_index], X[test_index]
        y_train, y_test = y[train_index], y[test_index]
        X_sample, y_sample = sample(
            X_train,
            y_train,
            strategy=self.sampling_strategy,
            random_state=sample_random_state,
        )

        if self.calibration_model is not None:
            self.calibration_model.base_estimator = model
            model = self.calibration_model

        model.fit(X_sample, y_sample)

        if model_building:
            results_dict = {
                "model name": model_name,
                "sample id": sample_id,
                "pickled model": pickle.dumps(
                    model
                ),  # pickle model to reduce memory usage. use pickle.loads() to de-serialize
            }

        else:
            y_pred = safe_predict_proba(model, X_test)

            results_dict = {
                "model name": model_name,
                "sample id": sample_id,
                "fold id": fold_id,
                "y_pred": y_pred,
                "labels": y_test,
            }

        return results_dict

    def _combine_predictions_inner(self, list_of_dicts, modality):
        """
        Combine the predictions arising from the inner cross validation.
        """

        # dictionary to store predictions
        combined_predictions = {}
        # combine fold predictions for each model
        for model_name in self.base_predictors.keys():
            for sample_id in range(self.n_samples):
                model_predictions = np.concatenate(
                    list(
                        d["y_pred"]
                        for d in list_of_dicts
                        if d["model name"] == model_name and d["sample id"] == sample_id
                    )
                )
                combined_predictions[
                    modality, model_name, sample_id
                ] = model_predictions
        labels = np.concatenate(
            list(
                d["labels"]
                for d in list_of_dicts
                if d["model name"] == list(self.base_predictors.keys())[0]
                and d["sample id"] == 0
            )
        )
        combined_predictions = pd.DataFrame(combined_predictions).rename_axis(
            ["modality", "base predictor", "sample"], axis=1
        )
        combined_predictions["labels"] = labels
        return combined_predictions

    def _combine_predictions_outer(self, list_of_dicts, modality, model_building=False):
        """
        Combine the predictions arising from the inner cross validation.
        """

        if model_building:
            k_outer = 1
        else:
            k_outer = self.k_outer

        combined_predictions = []

        for fold_id in range(k_outer):
            predictions = {}
            for model_name in self.base_predictors.keys():
                for sample_id in range(self.n_samples):
                    model_predictions = list(
                        d["y_pred"]
                        for d in list_of_dicts
                        if d["fold id"] == fold_id
                        and d["model name"] == model_name
                        and d["sample id"] == sample_id
                    )
                    predictions[modality, model_name, sample_id] = model_predictions[0]
            predictions = pd.DataFrame(predictions)

            if not model_building:
                labels = [
                    d["labels"]
                    for d in list_of_dicts
                    if d["fold id"] == fold_id
                    and d["model name"] == list(self.base_predictors.keys())[0]
                    and d["sample id"] == 0
                ]
                predictions["labels"] = labels[0]

            combined_predictions.append(
                predictions.rename_axis(
                    ["modality", "base predictor", "sample"], axis=1
                )
            )

        return combined_predictions

    def save(self, path=None):
        """
        Save to path.

        Parameters
        ----------

        path : optional, default=None
            Path to save the EnsembleIntegration class object.
        """

        if path is None:
            path = f"EI.{self.project_name}"
        with open(path, "wb") as f:
            pickle.dump(self, f)
        print(f"\nSaved to {path}\n")

    @classmethod
    def load(cls, path):
        """
        Load from path.

        Parameters
        ----------

        path : str
            Path to load the EnsembleIntegration class object.
        """
        with open(path, "rb") as f:
            return pickle.load(f)
