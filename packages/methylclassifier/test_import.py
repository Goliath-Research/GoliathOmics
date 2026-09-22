#!/usr/bin/env python3

import sys
from pathlib import Path

# Add paths
methyl_classifier_dir = Path(__file__).parent / 'methyl_classifier'
sys.path.insert(0, str(methyl_classifier_dir))

print("Testing imports...")

try:
    from classifier import MethylClassifier
    print("✅ MethylClassifier imported successfully")
except ImportError as e:
    print(f"❌ Failed to import MethylClassifier: {e}")

try:
    from data_loader import DataLoader
    print("✅ DataLoader imported successfully")
except ImportError as e:
    print(f"❌ Failed to import DataLoader: {e}")

try:
    import methyl_utils
    print("✅ methyl_utils imported successfully")
except ImportError as e:
    print(f"❌ Failed to import methyl_utils: {e}")
