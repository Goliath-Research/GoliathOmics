#!/usr/bin/env python3
import h5py
import numpy as np

def examine_h5_file(file_path):
    print(f"Examining: {file_path}")
    with h5py.File(file_path, 'r') as f:
        print('Groups:', list(f.keys()))
        
        data_group = f['methylation_data']
        print('Is group:', isinstance(data_group, h5py.Group))
        
        if isinstance(data_group, h5py.Group):
            print('Datasets:', list(data_group.keys()))
            for k in data_group.keys():
                dataset = data_group[k]
                print(f'  {k}: dtype={dataset.dtype}, shape={dataset.shape}')
                # Check if it's a numpy array and what type
                data = dataset[:]
                print(f'    Data type: {type(data)}, numpy dtype: {data.dtype}')
                if len(data) > 0:
                    print(f'    Sample values: {data[:3]}')
        else:
            print('Is dataset with dtype:', data_group.dtype)
            # Check if it's structured
            if data_group.dtype.names:
                print('Structured array fields:', data_group.dtype.names)
                for field in data_group.dtype.names:
                    print(f'  {field}: {data_group.dtype.fields[field]}')

if __name__ == "__main__":
    # Examine a failing file
    examine_h5_file('/home/ubuntu/Work/samples/arabidopsis/studyXXX/data/A68/1-CG.h5')
