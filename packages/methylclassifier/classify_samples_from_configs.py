#!/usr/bin/env python3
"""
Script to classify samples from JSON config files using trained models.

This script reads JSON config files that contain lists of sample directories,
loads the corresponding .h5 files, and classifies them using the appropriate
trained models for each chromosome-context combination.
"""

import json
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import numpy as np

# Add the methyl_classifier directory to path
methyl_classifier_dir = Path(__file__).parent / 'methyl_classifier'
sys.path.insert(0, str(methyl_classifier_dir))

# Import the modules directly
from classifier import MethylClassifier
from data_loader import DataLoader


def load_json_config(config_path: Path) -> Dict:
    """Load and parse a JSON config file."""
    with open(config_path, 'r') as f:
        return json.load(f)


def find_h5_file_in_sample_dir(sample_dir: Path, chrom: str, context: str) -> Optional[Path]:
    """Find the .h5 file matching chrom-context in a sample directory."""
    expected_filename = f"{chrom}-{context}.h5"
    h5_file = sample_dir / expected_filename
    return h5_file if h5_file.exists() else None


def process_chrom_context_configs(
    config_dir: Path,
    model_dir: Path,
    chrom: str,
    context: str
) -> List[Dict]:
    """
    Process all samples for a specific chromosome-context combination.

    Returns a list of results dictionaries.
    """
    results = []

    # Find the config file
    config_filename = f"{chrom}-{context}_config.json"
    config_path = config_dir / config_filename

    if not config_path.exists():
        print(f"⚠️ Config file not found: {config_path}")
        return results

    print(f"\n📄 Processing config: {config_path}")

    # Load config
    try:
        config = load_json_config(config_path)
    except Exception as e:
        print(f"❌ Failed to load config {config_path}: {e}")
        return results

    # Get sample directories
    sample_dirs = config.get('samples', [])
    if not sample_dirs:
        print(f"⚠️ No samples found in config {config_path}")
        return results

    print(f"📊 Found {len(sample_dirs)} sample directories")

    # Load the corresponding model
    model_filename = f"classifier-{chrom}-{context}.pkl"
    model_path = model_dir / model_filename

    if not model_path.exists():
        print(f"❌ Model file not found: {model_path}")
        return results

    print(f"🤖 Loading model: {model_path}")

    # Load classifier
    classifier = MethylClassifier()
    try:
        classifier.load_classifier(model_path)
        print(f"✅ Model loaded successfully")
        print(f"   Classes: {classifier.class_names}")
        print(f"   DMPs: {classifier.metadata.get('n_dmps', 'unknown')}")
    except Exception as e:
        print(f"❌ Failed to load model {model_path}: {e}")
        return results

    # Process each sample
    successful_classifications = 0

    for sample_dir_path in sample_dirs:
        sample_dir = Path(sample_dir_path)

        if not sample_dir.exists():
            print(f"⚠️ Sample directory not found: {sample_dir}")
            continue

        # Find the .h5 file
        h5_file = find_h5_file_in_sample_dir(sample_dir, chrom, context)
        if not h5_file:
            print(f"⚠️ .h5 file not found in {sample_dir} (expected: {chrom}-{context}.h5)")
            continue

        # Load and classify the sample
        try:
            sample = DataLoader.load_sample(h5_file)

            # Extract features
            feature_info = classifier.get_feature_info()
            n_dmps = feature_info['n_features']
            dmp_positions = feature_info['positions']

            features, mask, stats = DataLoader.extract_sample_features(sample, dmp_positions)

            # Classify
            predictions, probabilities = classifier.predict_proba(features.reshape(1, -1), mask.reshape(1, -1))

            prediction = predictions[0]
            probability = probabilities[0]

            # Get class name
            if classifier.class_names and prediction < len(classifier.class_names):
                predicted_class = classifier.class_names[prediction]
            else:
                predicted_class = f"Class_{prediction}"

            # Create result entry
            result = {
                'chrom': chrom,
                'context': context,
                'sample_dir': str(sample_dir),
                'sample_name': sample_dir.name,
                'h5_file': str(h5_file),
                'prediction': int(prediction),
                'predicted_class': predicted_class,
                'probability_class0': float(probability[0]),
                'probability_class1': float(probability[1]),
                'dmp_coverage': float(np.sum(mask) / len(mask) * 100),
                'sample_type': sample.sample_type,
                'avg_coverage': float(stats.get('avg_coverage', 0))
            }

            results.append(result)
            successful_classifications += 1

            print(f"  ✅ {sample_dir.name}: {predicted_class} "
                  f"(P0={probability[0]:.3f}, P1={probability[1]:.3f}, "
                  f"DMPs={np.sum(mask)}/{len(mask)})")

        except Exception as e:
            print(f"  ❌ Failed to classify {sample_dir.name}: {e}")
            continue

    print(f"📊 Successfully classified {successful_classifications}/{len(sample_dirs)} samples for {chrom}-{context}")

    return results


def load_config(config_file: Path) -> Dict:
    """Load configuration from YAML file."""
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    # Convert paths to Path objects
    config['config_dir'] = Path(config['config_dir'])
    config['model_dir'] = Path(config['model_dir'])
    if config.get('output_file'):
        config['output_file'] = Path(config['output_file'])

    return config


def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Classify samples from JSON config files using trained models"
    )

    parser.add_argument(
        '--config', '-c',
        type=Path,
        default=Path('classify_config.yaml'),
        help='Configuration file (default: classify_config.yaml)'
    )

    args = parser.parse_args()

    # Load configuration
    if not args.config.exists():
        print(f"❌ Configuration file not found: {args.config}")
        print("Please create a configuration file or specify one with --config")
        sys.exit(1)

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        sys.exit(1)

    # Validate directories
    if not config['config_dir'].exists():
        print(f"❌ Config directory not found: {config['config_dir']}")
        sys.exit(1)

    if not config['model_dir'].exists():
        print(f"❌ Model directory not found: {config['model_dir']}")
        sys.exit(1)

    # Find all config files
    config_files = list(config['config_dir'].glob("*_config.json"))

    if not config_files:
        print(f"❌ No config files found in {config['config_dir']}")
        sys.exit(1)

    print(f"📁 Found {len(config_files)} config files")

    # Extract chromosome-context combinations
    chrom_context_pairs = []
    for config_file in config_files:
        filename = config_file.stem  # Remove .json extension
        if filename.endswith('_config'):
            chrom_ctx = filename[:-7]  # Remove '_config'
            try:
                chrom, context = chrom_ctx.split('-')
                chrom_context_pairs.append((chrom, context))
            except ValueError:
                print(f"⚠️ Could not parse chrom-context from {filename}")
                continue

    # Filter by user-specified chrom/context if provided
    if config.get('chromosome') or config.get('context'):
        filtered_pairs = []
        for chrom, context in chrom_context_pairs:
            if config.get('chromosome') and chrom != config['chromosome']:
                continue
            if config.get('context') and context != config['context']:
                continue
            filtered_pairs.append((chrom, context))
        chrom_context_pairs = filtered_pairs

    if not chrom_context_pairs:
        print("❌ No matching chromosome-context combinations found")
        sys.exit(1)

    print(f"🎯 Processing {len(chrom_context_pairs)} chromosome-context combinations")

    # Process all combinations
    all_results = []

    for chrom, context in sorted(chrom_context_pairs):
        results = process_chrom_context_configs(
            config['config_dir'], config['model_dir'], chrom, context
        )
        all_results.extend(results)

    # Summary
    print(f"\n📊 SUMMARY")
    print(f"Total samples processed: {len(all_results)}")

    if all_results:
        # Group by chrom-context
        by_chrom_ctx = {}
        for result in all_results:
            key = f"{result['chrom']}-{result['context']}"
            if key not in by_chrom_ctx:
                by_chrom_ctx[key] = []
            by_chrom_ctx[key].append(result)

        print(f"Chromosome-context combinations: {len(by_chrom_ctx)}")

        for chrom_ctx, results in sorted(by_chrom_ctx.items()):
            class_counts = {}
            for result in results:
                cls = result['predicted_class']
                class_counts[cls] = class_counts.get(cls, 0) + 1

            print(f"  {chrom_ctx}: {len(results)} samples - {class_counts}")

        # Save to CSV if requested
        if config.get('output_file'):
            import csv

            config['output_file'].parent.mkdir(parents=True, exist_ok=True)

            with open(config['output_file'], 'w', newline='') as csvfile:
                fieldnames = [
                    'chrom', 'context', 'sample_name', 'predicted_class',
                    'probability_class0', 'probability_class1', 'dmp_coverage',
                    'sample_type', 'avg_coverage'
                ]

                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_results)

            print(f"💾 Results saved to: {config['output_file']}")

    # Display model descriptions at the beginning
    print(f"\n🤖 MODEL DESCRIPTIONS:")
    for chrom, context in sorted(set((r['chrom'], r['context']) for r in all_results)):
        model_path = config['model_dir'] / f"classifier-{chrom}-{context}.pkl"
        if model_path.exists():
            classifier = MethylClassifier()
            try:
                classifier.load_classifier(model_path)
                print(f"  {chrom}-{context}: {classifier.class_names[0]} vs {classifier.class_names[1]} "
                      f"({classifier.metadata.get('n_dmps', 'unknown')} DMPs)")
            except Exception as e:
                print(f"  {chrom}-{context}: Failed to load model info - {e}")


if __name__ == '__main__':
    main()
