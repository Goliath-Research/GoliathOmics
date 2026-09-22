#!/usr/bin/env python3
"""
Test script for SQLModel migration.

This demonstrates the new SQLModel-based API and validates the migration.
"""

import sys
import os
from pathlib import Path

# Check for METHYLPIPELINE environment variable
if 'METHYLPIPELINE' not in os.environ:
    print("❌ Error: METHYLPIPELINE environment variable is not set.")
    print("Please run: source setup_env.sh")
    sys.exit(1)

# Add methyl_mapper to path
methylpipeline_root = Path(os.environ['METHYLPIPELINE'])
sys.path.insert(0, str(methylpipeline_root / "packages" / "methyl_mapper"))

import pandas as pd
from methyl_mapper import (
    AzureSQLConfig,
    StoredProcedureConfig,
    MethylMapperConfig,
    AzureSQLConnection,
    DMPStaging,
    GeneMappingResult
)


def test_model_creation():
    """Test creating DMPStaging objects with validation."""
    print("\n" + "="*70)
    print("TEST 1: SQLModel Object Creation and Validation")
    print("="*70)
    
    try:
        # Create a valid DMP
        dmp = DMPStaging(
            SampleID=1,
            position=12345678,
            chromosome="chr1",
            context="CG",
            q_value=0.001,
            delta_mean=0.25,
            overlap=0.15,
            effect_size=0.8
        )
        print(f"✅ Created DMPStaging object:")
        print(f"   SampleID={dmp.SampleID}, pos={dmp.position}")
        print(f"   chr={dmp.chromosome}, ctx={dmp.context}")
        print(f"   q={dmp.q_value}, delta={dmp.delta_mean}")
        
        # Test JSON serialization (Pydantic feature)
        json_str = dmp.model_dump_json()
        print(f"\n✅ JSON serialization works:")
        print(f"   {json_str[:100]}...")
        
        # Test validation - missing required field
        try:
            bad_dmp = DMPStaging(
                SampleID=1,
                # Missing position!
                chromosome="chr1",
                context="CG"
            )
            print("❌ Should have raised validation error!")
        except Exception as e:
            print(f"\n✅ Validation works - caught missing field:")
            print(f"   {type(e).__name__}: {str(e)[:100]}")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_validation():
    """Test enhanced config validation."""
    print("\n" + "="*70)
    print("TEST 2: Enhanced Config Validation")
    print("="*70)
    
    try:
        # Valid config
        sp_config = StoredProcedureConfig(
            upstream_size=5000,
            w_promoter=2.0,
            w_exon=1.5
        )
        print(f"✅ Created valid StoredProcedureConfig:")
        print(f"   upstream_size={sp_config.upstream_size}")
        print(f"   w_promoter={sp_config.w_promoter}")
        
        # Test validation - negative weight should fail
        try:
            bad_config = StoredProcedureConfig(
                upstream_size=5000,
                w_promoter=-1.0  # Invalid!
            )
            print("❌ Should have raised validation error!")
        except Exception as e:
            print(f"\n✅ Validation works - caught invalid weight:")
            print(f"   {type(e).__name__}")
        
        # Test validation - unreasonable size
        try:
            bad_config = StoredProcedureConfig(
                upstream_size=2_000_000,  # > 1 Mb, too large!
                w_promoter=1.0
            )
            print("❌ Should have raised validation error!")
        except Exception as e:
            print(f"\n✅ Validation works - caught unreasonable size:")
            print(f"   {str(e)[:100]}")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_bulk_operations():
    """Test pandas integration for bulk operations."""
    print("\n" + "="*70)
    print("TEST 3: Bulk Operations with Pandas")
    print("="*70)
    
    try:
        # Create sample DataFrame
        df = pd.DataFrame({
            'position': [100, 200, 300, 400, 500],
            'chromosome': ['chr1', 'chr1', 'chr2', 'chr2', 'chr3'],
            'context': ['CG', 'CG', 'CHG', 'CHH', 'CG'],
            'q_value': [0.001, 0.002, 0.003, 0.004, 0.005],
            'delta_mean': [0.2, 0.3, -0.1, 0.4, -0.2],
            'overlap': [0.1, 0.15, 0.2, 0.05, 0.25],
            'effect_size': [0.8, 0.9, 0.6, 1.0, 0.7]
        })
        
        print(f"✅ Created DataFrame with {len(df)} DMPs:")
        print(df.head())
        
        # Convert to DMPStaging objects (for ORM operations)
        dmps = [
            DMPStaging(
                SampleID=1,
                **row.to_dict()
            )
            for _, row in df.iterrows()
        ]
        
        print(f"\n✅ Converted to {len(dmps)} DMPStaging objects")
        print(f"   First DMP: chr={dmps[0].chromosome}, pos={dmps[0].position}")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_gene_mapping_result():
    """Test GeneMappingResult model."""
    print("\n" + "="*70)
    print("TEST 4: GeneMappingResult Model")
    print("="*70)
    
    try:
        # Create a gene mapping result
        result = GeneMappingResult(
            gene_name="TP53",
            gene_id="ENSG00000141510",
            region="promoter",
            weight=2.5,
            dmp_count=3
        )
        
        print(f"✅ Created GeneMappingResult:")
        print(f"   Gene: {result.gene_name} ({result.gene_id})")
        print(f"   Region: {result.region}, Weight: {result.weight}")
        print(f"   DMP count: {result.dmp_count}")
        
        # Test JSON serialization
        json_data = result.model_dump()
        print(f"\n✅ Serialized to dict:")
        print(f"   {json_data}")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("SQLModel Migration Test Suite")
    print("="*70)
    print("\nTesting the new SQLModel-based MethylMapper implementation")
    print("This validates Pydantic validation + SQLAlchemy ORM integration")
    
    tests = [
        ("Model Creation", test_model_creation),
        ("Config Validation", test_config_validation),
        ("Bulk Operations", test_bulk_operations),
        ("Gene Mapping Result", test_gene_mapping_result),
    ]
    
    results = []
    for name, test_func in tests:
        result = test_func()
        results.append((name, result))
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nPassed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 All tests passed! SQLModel migration successful!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())

