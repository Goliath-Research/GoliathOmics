"""
Build SP-equivalent region BED from GTF for BedtoolsMapper.
Produces gene body, promoter, terminator, exon, intron regions with weights.
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# BED is 0-based half-open; GTF is 1-based closed. So 1-based [a,b] -> BED (a-1, b)
def _gtf_to_bed_start_end(gtf_start: int, gtf_end: int) -> Tuple[int, int]:
    return (gtf_start - 1, gtf_end)


def _parse_gtf_attrs(attr_str: str) -> Dict[str, str]:
    out = {}
    for part in attr_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if " " in part:
            k, v = part.split(" ", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def build_sp_regions_bed(
    gtf_path: Path,
    output_bed: Path,
    upstream_size: int = 5000,
    downstream_size: int = 2000,
    min_intron_size: int = 0,
    w_promoter: float = 2.0,
    w_terminator: float = 0.5,
    w_gene_body: float = 1.0,
    w_exon: float = 1.5,
    w_intron: float = 0.7,
) -> Path:
    """
    Build a BED file of weighted regions (gene body, promoter, terminator, exon, intron)
    from a GTF file. BED columns: chrom, start, end, name (gene_id|gene_name|feature_type), score, strand.

    Args:
        gtf_path: Path to GTF file
        output_bed: Path to write BED
        upstream_size: Promoter size (bp upstream of TSS)
        downstream_size: Terminator size (bp downstream of TES)
        min_intron_size: Minimum intron length to emit
        w_*: Weights for each region type (stored in BED score column)

    Returns:
        output_bed path
    """
    genes: List[Dict] = []  # seqname, start, end, strand, gene_id, gene_name
    exons: List[Dict] = []   # seqname, start, end, strand, gene_id, transcript_id

    with open(gtf_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 9:
                continue
            seqname, source, feature, start_s, end_s, score, strand, frame, attrs = parts[:9]
            start = int(start_s)
            end = int(end_s)
            a = _parse_gtf_attrs(attrs)
            gene_id = a.get("gene_id") or a.get("gene_name") or ""
            gene_name = a.get("gene_name") or gene_id
            if not gene_id:
                continue

            if feature == "gene":
                genes.append({
                    "seqname": seqname,
                    "start": start,
                    "end": end,
                    "strand": strand if strand in ("+", "-") else "+",
                    "gene_id": gene_id,
                    "gene_name": gene_name,
                })
            elif feature == "exon":
                exons.append({
                    "seqname": seqname,
                    "start": start,
                    "end": end,
                    "strand": strand if strand in ("+", "-") else "+",
                    "gene_id": gene_id,
                    "gene_name": gene_name,
                    "transcript_id": a.get("transcript_id", ""),
                })

    # Sort genes by position for promoter/terminator clipping (simplified: no clipping for now)
    rows: List[Tuple[str, int, int, str, float, str]] = []  # chrom, start, end, name, score, strand

    for g in genes:
        chrom = g["seqname"]
        s, e = g["start"], g["end"]
        strand = g["strand"]
        gid, gname = g["gene_id"], g["gene_name"]
        # Gene body
        bstart, bend = _gtf_to_bed_start_end(s, e)
        rows.append((chrom, bstart, bend, f"{gid}|{gname}|gene_body", w_gene_body, strand))
        # Promoter: upstream of TSS. BED 0-based half-open.
        # + strand TSS = start; promoter = 1-based [start - upstream_size, start - 1]
        if strand == "+":
            p_start = max(0, s - upstream_size - 1)
            p_end = s  # exclusive: 0-based [p_start, p_end) = 1-based [p_start+1, p_end] = [s-upstream_size, s-1] if p_start=s-upstream_size-1
            if p_end > p_start:
                rows.append((chrom, p_start, p_end, f"{gid}|{gname}|promoter", w_promoter, strand))
        else:
            # - strand TSS = end; promoter = 1-based [end+1, end+upstream_size]
            p_start = e
            p_end = e + upstream_size
            if p_end > p_start:
                rows.append((chrom, p_start, p_end, f"{gid}|{gname}|promoter", w_promoter, strand))
        # Terminator: downstream of TES.
        # + strand TES = end; terminator = 1-based [end+1, end+downstream_size] -> BED (end, end+downstream_size)
        if strand == "+":
            t_start = e
            t_end = e + downstream_size
            if t_end > t_start:
                rows.append((chrom, t_start, t_end, f"{gid}|{gname}|terminator", w_terminator, strand))
        else:
            # - strand TES = start; terminator = 1-based [start - downstream_size, start - 1]
            t_start = max(0, s - downstream_size - 1)
            t_end = s
            if t_end > t_start:
                rows.append((chrom, t_start, t_end, f"{gid}|{gname}|terminator", w_terminator, strand))

    # Exons and introns (per transcript)
    by_tx: Dict[str, List[Dict]] = {}
    for ex in exons:
        tx = ex["transcript_id"] or ex["gene_id"]
        by_tx.setdefault(tx, []).append(ex)
    for tx, ex_list in by_tx.items():
        ex_list.sort(key=lambda x: (x["start"], x["end"]))
        gid = ex_list[0]["gene_id"]
        gname = ex_list[0]["gene_name"]
        chrom = ex_list[0]["seqname"]
        strand = ex_list[0]["strand"]
        for ex in ex_list:
            bstart, bend = _gtf_to_bed_start_end(ex["start"], ex["end"])
            rows.append((chrom, bstart, bend, f"{gid}|{gname}|exon", w_exon, strand))
        for i in range(len(ex_list) - 1):
            intron_start = ex_list[i]["end"] + 1
            intron_end = ex_list[i + 1]["start"] - 1
            if intron_end - intron_start + 1 >= min_intron_size:
                rows.append((chrom, intron_start, intron_end, f"{gid}|{gname}|intron", w_intron, strand))

    output_bed.parent.mkdir(parents=True, exist_ok=True)
    with open(output_bed, "w") as out:
        for chrom, bstart, bend, name, score, strand in sorted(rows, key=lambda r: (r[0], r[1], r[2])):
            out.write(f"{chrom}\t{bstart}\t{bend}\t{name}\t{score}\t{strand}\n")

    logger.info(f"Wrote {len(rows)} SP-equivalent regions to {output_bed}")
    return output_bed


def build_gene_bodies_bed(gtf_path: Path, output_bed: Path) -> Path:
    """
    Write a 6-column BED of GTF gene features only (chrom, start, end, name, score, strand).
    Name column is \"gene_id|gene_name\" for downstream closest / overlap.
    """
    rows: List[Tuple[str, int, int, str, int, str]] = []
    with open(gtf_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 9:
                continue
            seqname, _source, feature, start_s, end_s, _score, strand, _frame, attrs = parts[:9]
            if feature != "gene":
                continue
            start = int(start_s)
            end = int(end_s)
            a = _parse_gtf_attrs(attrs)
            gene_id = a.get("gene_id") or a.get("gene_name") or ""
            gene_name = a.get("gene_name") or gene_id
            if not gene_id:
                continue
            bstart, bend = _gtf_to_bed_start_end(start, end)
            st = strand if strand in ("+", "-") else "+"
            name = f"{gene_id}|{gene_name}"
            rows.append((seqname, bstart, bend, name, 0, st))
    output_bed.parent.mkdir(parents=True, exist_ok=True)
    with open(output_bed, "w") as out:
        for chrom, bstart, bend, name, score, st in sorted(rows, key=lambda r: (r[0], r[1], r[2])):
            out.write(f"{chrom}\t{bstart}\t{bend}\t{name}\t{score}\t{st}\n")
    logger.info(f"Wrote {len(rows)} gene bodies to {output_bed}")
    return output_bed


def parse_region_name(bed_name: str) -> Tuple[str, str, str]:
    """Parse BED 4th column into gene_id, gene_name, feature_type."""
    parts = bed_name.split("|", 2)
    gene_id = parts[0] if len(parts) > 0 else ""
    gene_name = parts[1] if len(parts) > 1 else gene_id
    feature_type = parts[2] if len(parts) > 2 else "gene_body"
    return gene_id, gene_name, feature_type
