from typing import Dict, Any
from .beta_classifier import BetaClassifier
from .beta_binomial_classifier import BetaBinomialClassifier

class ClassifierFactory:
    @classmethod
    def create(cls, classifier_type: str, data: Dict, **kwargs) -> 'BaseClassifier':
        if classifier_type == "beta":
            return BetaClassifier(data, **kwargs)
        elif classifier_type == "beta_binomial":
            print("Warning: Beta-Binomial discouraged; use for low-coverage only.")
            return BetaBinomialClassifier(data, **kwargs)
        else:
            raise ValueError(f"Invalid classifier_type: {classifier_type}. Must be 'beta' or 'beta_binomial'.")

# BaseClassifier can be an abstract base or just use typing
from typing import Union
BaseClassifier = Union[BetaClassifier, BetaBinomialClassifier]
