# Ensemble Integration (EI): Integrating multimodal data through interpretable heterogeneous ensembles
Ensemble Integration (EI) is a customizable pipeline for generating diverse ensembles of heterogeneous classifiers, as well as the accompanying metadata needed for ensemble learning approaches utilizing ensemble diversity for improved performance. It also fairly evaluates the performance of several ensemble learning methods including stacked generalization (stacking) [Wolpert1992]. Though other tools exist, we are unaware of a similar modular, scalable pipeline designed for large-scale ensemble learning. 

This fully python version of EI was implemented by Jamie J. R. Bennett and Yan Chak Li, and is based on the original version: https://github.com/GauravPandeyLab/ensemble_integration. The algorithm is identical, however, in this version we rely on sklearn models rather than weka models (note though that some weka models are not available in sklearn and vice versa).

EI is designed for generating extremely large ensembles (taking days or weeks to generate) and thus consists of an initial data generation phase tuned for multicore and distributed computing environments. The output is a set of compressed CSV files containing the class distribution produced by each classifier that serves as input to a later ensemble learning phase.

More details of EI can be found in our Biorxiv preprint:

Full citation:

Yan Chak Li, Linhua Wang, Jeffrey N Law, T M Murali, Gaurav Pandey, Integrating multimodal data through interpretable heterogeneous ensembles, Bioinformatics Advances, Volume 2, Issue 1, 2022, vbac065, https://doi.org/10.1093/bioadv/vbac065

This repository is protected by [CC BY-NC 4.0](/license.md).

Upcoming feature: ensemble selection [Caruana2004] in the ensemble stage.

## Requirements ##

The following packages are requirements for EI:

```
python==3.9.12
scikit-learn==1.1.1
pandas==1.4.3
numpy==1.22.3
joblib==1.1.0
```

## Demo ##

Import scikit-learn classifiers and EI.

```
from sklearn.ensemble import AdaBoostClassifier, GradientBoostingClassifier, RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.preprocessing import RobustScaler
from sklearn.svm import LinearSVC
from sklearn.neural_network import MLPClassifier
from xgboost import XGBClassifier
import sys

path_to_ei = "/path/to/ei-python/"
sys.path.append(path_to_ei)
from ei import EnsembleIntegration
```

Generate a toy multimodal dataset.

```
from sklearn.dataset import make_classification

X, y = make_classification(n_samples=1000, n_features=20, n_redundant=0,
n_clusters_per_class=1, weights=[0.7], flip_y=0, random_state=1)

X_view_0 = X[:, :5]
X_view_1 = X[:, 5:10]
X_view_2 = X[:, 10:15]
X_view_3 = X[:, 15:]
  
```

Define base predictors as a dictionary.

```
base_predictors = {
    'AdaBoost': AdaBoostClassifier(base_estimator=DecisionTreeClassifier(max_depth=3)),
    'DT': DecisionTreeClassifier(max_depth=3),
    'GradientBoosting': GradientBoostingClassifier(),
    'KNN': KNeighborsClassifier(n_neighbors=21),
    'LR': LogisticRegression(),
    'NB': GaussianNB(),
    'MLP': MLPClassifier(),
    'RF': RandomForestClassifier(),
    'SVM': LinearSVC(),
    'XGB': XGBClassifier(use_label_encoder=False, eval_metric='error')
}
```

Set up a 5-fold outer cross validation, along with a 5-fold inner cross validation for meta-training data generation. Meta models can be defined here, or later. 

```
EI = EnsembleIntegration(base_predictors=base_predictors,
                         k_outer=5,
                         k_inner=5,
                         n_samples=1,
                         sampling_strategy="undersampling",
                         sampling_aggregation="mean",
                         n_jobs=-1, # set as -1 to use all available CPUs
                         random_state=42,
                         project_name="demo")
```

Generate meta-training data and train base classifiers on outer folds.

```
modalities = ["view_0", "view_1", "view_2", "view_3"]

for modality in modalities:
    X, y = read_data(f"data/{modality}/data.arff")
    EI.train_base(X, y, base_predictors, modality=modality)

EI.save() # save EI as EI.demo
```

Define stacking classifiers and test ensembles.

```
meta_models = {
    "AdaBoost": AdaBoostClassifier(),
    "DT": DecisionTreeClassifier(max_depth=5),
    "GradientBoosting": GradientBoostingClassifier(),
    "KNN": KNeighborsClassifier(n_neighbors=21),
    "LR": LogisticRegression(),
    "NB": GaussianNB(),
    "MLP": MLPClassifier(),
    "RF": RandomForestClassifier(),
    "SVM": LinearSVC(tol=1e-2, max_iter=10000),
    "XGB": XGBClassifier(use_label_encoder=False, eval_metric='error')
}

EI = EnsembleIntegration().load("EI.demo") # load models from disk

EI.train_meta(meta_models=meta_models) # train meta classifiers
```

# Sample data

We uploaded the sample data used in the paper to [zenodo](https://doi.org/10.5281/zenodo.6972512).

The compressed zip files `PFP.zip` contains the input data used for EI.

For PFP, since the raw data is very large (around 2139 * 2GB), we uploaded 5 samples of the GO terms which have been transformed into the format for EI. The remaining terms can be generated by the STRING DB (`PFP/STRING_csv`) & GO annotation files (`GO_annotation.tsv`) using `generate_data.py`

For example, you may generate the input data for predicting `GO:0000166` by the following command:

	python processing_scripts/generate_data.py --outcome GO:0000166 
	
# Model Interpretation

See the [demostration notebook](/interpretation_demo.ipynb). Note that the implementation is slightly different from getting feature importance from Weka since using different function. The results may vary between two implementations.
