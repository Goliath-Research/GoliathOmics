#!/bin/bash

# Simple wrapper script to run classification inside the goliath container

echo "Running MethylClassifier inside goliath container..."

docker exec goliath bash -c "
cd /home/ubuntu/MethylClassifier && \
PYTHONPATH=/home/ubuntu/MethylClassifier:/home/ubuntu/MethylUtils:/home/ubuntu/MethylPipeline/packages/methyldetector python3 -c \"
import sys
sys.path.insert(0, '/home/ubuntu/MethylClassifier')
sys.path.insert(0, '/home/ubuntu/MethylUtils')
sys.path.insert(0, '/home/ubuntu/MethylPipeline/packages/methyldetector')

import yaml
import json
from pathlib import Path
from classifier import MethylClassifier
from data_loader import DataLoader

# Load config
with open('classify_config.yaml', 'r') as f:
    config = yaml.safe_load(f)

print('🤖 MODEL DESCRIPTIONS:')
model_dir = Path(config['model_dir'])
for model_file in sorted(model_dir.glob('classifier-*-*.pkl')):
    filename = model_file.stem
    chrom_ctx = filename.replace('classifier-', '')
    try:
        chrom, context = chrom_ctx.split('-')
        classifier = MethylClassifier()
        classifier.load_classifier(model_file)
        print(f'  {chrom}-{context}: {classifier.class_names[0]} vs {classifier.class_names[1]} ({classifier.metadata.get(\"n_dmps\", \"unknown\")} DMPs)')
    except Exception as e:
        print(f'  {chrom}-{context}: Failed to load - {e}')

print('\n📄 Processing samples...')
config_dir = Path(config['config_dir'])
results = []

for config_file in sorted(config_dir.glob('*_config.json')):
    filename = config_file.stem
    chrom_ctx = filename.replace('_config', '')
    try:
        chrom, context = chrom_ctx.split('-')
        print(f'\nProcessing {chrom}-{context}...')
        
        # Load JSON config
        with open(config_file, 'r') as f:
            json_config = json.load(f)
        
        # Load model
        model_path = model_dir / f'classifier-{chrom}-{context}.pkl'
        classifier = MethylClassifier()
        classifier.load_classifier(model_path)
        
        # Process samples
        sample_dirs = json_config.get('samples', [])
        successful = 0
        
        for sample_dir_path in sample_dirs[:5]:  # Limit to first 5 for testing
            sample_dir = Path(sample_dir_path)
            h5_file = sample_dir / f'{chrom}-{context}.h5'
            
            if h5_file.exists():
                try:
                    sample = DataLoader.load_sample(h5_file)
                    feature_info = classifier.get_feature_info()
                    features, mask, stats = DataLoader.extract_sample_features(sample, feature_info['positions'])
                    
                    predictions, probabilities = classifier.predict_proba(features.reshape(1, -1), mask.reshape(1, -1))
                    prediction = predictions[0]
                    probability = probabilities[0]
                    
                    predicted_class = classifier.class_names[prediction] if classifier.class_names else f'Class_{prediction}'
                    
                    results.append({
                        'chrom': chrom,
                        'context': context,
                        'sample': sample_dir.name,
                        'predicted_class': predicted_class,
                        'prob_class0': float(probability[0]),
                        'prob_class1': float(probability[1]),
                        'dmp_coverage': float(np.sum(mask) / len(mask) * 100)
                    })
                    
                    print(f'  ✅ {sample_dir.name}: {predicted_class} (P0={probability[0]:.3f}, P1={probability[1]:.3f})')
                    successful += 1
                    
                except Exception as e:
                    print(f'  ❌ {sample_dir.name}: {e}')
            else:
                print(f'  ⚠️ {sample_dir.name}: H5 file not found')
        
        print(f'  Successfully classified {successful}/{len(sample_dirs[:5])} samples')
        
    except Exception as e:
        print(f'❌ Failed to process {chrom_ctx}: {e}')

# Summary
print(f'\n📊 SUMMARY:')
print(f'Total samples processed: {len(results)}')

if results:
    by_chrom_ctx = {}
    for r in results:
        key = f\"{r['chrom']}-{r['context']}\"
        if key not in by_chrom_ctx:
            by_chrom_ctx[key] = []
        by_chrom_ctx[key].append(r)
    
    for chrom_ctx, res_list in sorted(by_chrom_ctx.items()):
        class_counts = {}
        for r in res_list:
            cls = r['predicted_class']
            class_counts[cls] = class_counts.get(cls, 0) + 1
        print(f'  {chrom_ctx}: {len(res_list)} samples - {class_counts}')

    # Save to CSV if specified
    if config.get('output_file'):
        import csv
        output_path = Path(config['output_file'])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', newline='') as csvfile:
            fieldnames = ['chrom', 'context', 'sample', 'predicted_class', 'prob_class0', 'prob_class1', 'dmp_coverage']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f'💾 Results saved to: {output_path}')
\"
