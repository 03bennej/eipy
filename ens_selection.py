from utils import scores, set_seed, \
    random_integers, sample, \
    retrieve_X_y, append_modality, f_minor_sklearn, f_minority_score

from numpy.random import choice, seed
import numpy as np
from numpy import argmax, argmin, argsort, corrcoef, mean, nanmax, sqrt, triu_indices_from, where
import pandas as pd

from sklearn.utils.validation import check_X_y, check_array, check_is_fitted
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.multiclass import unique_labels

class CES(BaseEstimator, ClassifierMixin):
    """
    Caruana et al's Ensemble Selection
    """
    def __init__(self,
                 scoring_func=f_minority_score,
                 max_ensemble_size=50,
                 random_state=0,
                 greater_is_better=True
                 ):
        set_seed(random_state)
        self.seed = random_state
        self.scoring_func = scoring_func
        self.max_ensemble_size = max_ensemble_size
        self.selected_ensemble = []
        self.train_performance = []
        self.greater_is_better = greater_is_better
        self.argbest = argmax if greater_is_better else argmin
        self.best = max if greater_is_better else min
        self.random_state = random_state
       

    def fit(self, X, y):

        # X, y = check_X_y(X, y)
        # Store the classes seen during fit
        self.classes_ = unique_labels(y)

        self.X_ = X
        self.y_ = y
        # Return the classifier
        
        self.selected_ensemble = []
        self.train_performance = []
        # print(X, y)
        self.rng_generator = np.random.default_rng(seed=self.random_state)
        best_classifiers = X.apply(lambda x: self.scoring_func(y, x)).sort_values(ascending=self.greater_is_better)
        for i in range(min(self.max_ensemble_size, len(best_classifiers))):
            best_candidate = self.select_candidate_enhanced(X, y, best_classifiers, self.selected_ensemble, i)
            self.selected_ensemble.append(best_candidate)
            self.train_performance.append(self.get_performance(X, y))

        train_performance_df = pd.DataFrame.from_records(self.train_performance)
        best_ensemble_size = self.get_best_performer(train_performance_df)['ensemble_size'].values
        self.best_ensemble = train_performance_df['ensemble'][:best_ensemble_size.item(0) + 1]
        # print(self.best_ensemble)
        return self

    def predict_proba(self, X):
        # print(self.best_ensemble)
        check_is_fitted(self)
        # X = check_array(X)
        ces_bp_df = X[self.best_ensemble]
        predict_positive = ces_bp_df.mean(axis=1).values
        return np.transpose(np.array([1 - predict_positive, predict_positive]))

    def select_candidate_enhanced(self, X, y, best_classifiers, ensemble, i):
        initial_ensemble_size = 2
        max_candidates = 50
        if len(ensemble) >= initial_ensemble_size:
            candidates = self.rng_generator.choice(best_classifiers.index.values, min(max_candidates, len(best_classifiers)),
                                replace=False)
            candidate_scores = [self.scoring_func(y, X[ensemble + [candidate]].mean(axis=1)) for
                                candidate in
                                candidates]
            best_candidate = candidates[self.argbest(candidate_scores)]
        else:
            best_candidate = best_classifiers.index.values[i]
        return best_candidate

    def get_performance(self, X, y):

        predictions = X[self.selected_ensemble].mean(axis=1)
        score = self.scoring_func(y, predictions)

        return {'seed': self.seed, 'score': score,
                'ensemble': self.selected_ensemble[-1],
                'ensemble_size': len(self.selected_ensemble)}

    def get_best_performer(self, df, one_se=False):
        if not one_se:
            return df[df.score == self.best(df.score)].head(1)
        se = df.score.std() / sqrt(df.shape[0] - 1)
        if self.greater_is_better:
            return df[df.score >= (self.best(df.score) - se)].head(1)
        return df[df.score <= (self.best(df.score) + se)].head(1)


# class BestBasePredictor:
#      """
#      Picking the best base predictor
#      """
#     def __init__(self, scoring_func, greater_is_better=True):
#         self.scoring_func = scoring_func
#         self.greater_is_better = greater_is_better
#         self.argbest = argmax if greater_is_better else argmin
#         self.best = max if greater_is_better else min

#     def fit(self, X, y):
#         return df[df.score <= (self.best(df.score) + se)].head(1)
