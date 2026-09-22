"""Tests for production project path remapping."""

from pathlib import Path

from methyl_validation.path_remap import (
    apply_path_remap_to_nested,
    remap_cohort_list_files_in_project,
    remap_path_string,
)


def test_stale_samples_base_from_config_remapped_like_freeze():
    """CLI copies samples_base_path from JSON before --path-remap; freeze must not restore OLD prefix."""
    pr = {
        "/lambda/nfs/Work/prostate-cancer/samples": "/work/samples",
        "/lambda/nfs/Work/prostate-cancer": "/work/projects/prostate-cancer",
    }
    d = {"samples_base_path": "/lambda/nfs/Work/prostate-cancer/samples"}
    apply_path_remap_to_nested(d, pr)
    assert d["samples_base_path"] == "/work/samples"
    stale_from_config = "/lambda/nfs/Work/prostate-cancer/samples"
    d["samples_base_path"] = remap_path_string(stale_from_config.rstrip("/"), pr)
    assert d["samples_base_path"] == "/work/samples"


def test_remap_path_string_longest_prefix():
    m = {"/a": "/x", "/a/b": "/y"}
    assert remap_path_string("/a/b/c", m) == "/y/c"
    assert remap_path_string("/a/z", m) == "/x/z"


def test_apply_path_remap_to_nested_mutates():
    d = {
        "samples_base_path": "/old/root",
        "controls": {"groups": [{"sample_paths": ["/old/root/s1/x.csv"]}]},
    }
    apply_path_remap_to_nested(d, {"/old/root": "/new/root"})
    assert d["samples_base_path"] == "/new/root"
    assert d["controls"]["groups"][0]["sample_paths"] == ["/new/root/s1/x.csv"]


def test_remap_cohort_list_csv_rewrites_content_and_pointer(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    list_csv = data_dir / "cohort.csv"
    list_csv.write_text("sample\n/lambda/nfs/samples/a\n", encoding="utf-8")
    d = {"controls": {"groups": [{"sample_paths": [str(list_csv)]}]}}
    prod = tmp_path / "production"
    prod.mkdir()
    remap_cohort_list_files_in_project(
        d, {"/lambda/nfs": "/work"}, prod
    )
    new_p = Path(d["controls"]["groups"][0]["sample_paths"][0])
    assert new_p.is_file()
    assert new_p.parent.name == "_path_remap_cohort_lists"
    text = new_p.read_text(encoding="utf-8")
    assert "/work/samples/a" in text
    assert "/lambda/" not in text


def test_remap_methylvalidation_testing_csv_path_column(tmp_path: Path):
    """Same shape as project_gen.write_val_csv: header path, absolute sample directories."""
    p = tmp_path / "testing_pca.csv"
    p.write_text(
        "path\n/lambda/nfs/mount/samples/case_a\n/lambda/nfs/mount/samples/case_b\n",
        encoding="utf-8",
    )
    d = {"holdouts": {"sample_paths": [str(p)]}}
    prod = tmp_path / "production"
    prod.mkdir()
    remap_cohort_list_files_in_project(d, {"/lambda/nfs/mount": "/work/mount"}, prod)
    new_p = Path(d["holdouts"]["sample_paths"][0])
    assert new_p.is_file()
    body = new_p.read_text(encoding="utf-8")
    assert "/work/mount/samples/case_a" in body
    assert "/lambda/" not in body


def test_basename_only_cohort_csv_not_copied_samples_base_remapped(tmp_path: Path):
    """Lists of basenames are resolved via samples_base_path; list file body has no path prefixes."""
    lists = tmp_path / "lists"
    lists.mkdir()
    cohort = lists / "cases.csv"
    cohort.write_text("sample\nS001\n", encoding="utf-8")
    d = {
        "samples_base_path": "/lambda/nfs/mount/samples",
        "controls": {"groups": [{"sample_paths": [str(cohort)]}]},
    }
    apply_path_remap_to_nested(d, {"/lambda/nfs/mount": "/work/mount"})
    remap_cohort_list_files_in_project(d, {"/lambda/nfs/mount": "/work/mount"}, tmp_path / "prod")
    assert d["samples_base_path"] == "/work/mount/samples"
    assert d["controls"]["groups"][0]["sample_paths"] == [str(cohort)]
    assert cohort.read_text(encoding="utf-8") == "sample\nS001\n"


def test_remap_cohort_list_no_op_when_paths_unchanged(tmp_path: Path):
    p = tmp_path / "x.csv"
    p.write_text("sample\n/other/p\n", encoding="utf-8")
    d = {"sample_paths": [str(p)]}
    remap_cohort_list_files_in_project(d, {"/lambda/nfs": "/work"}, tmp_path / "prod")
    assert d["sample_paths"] == [str(p)]
