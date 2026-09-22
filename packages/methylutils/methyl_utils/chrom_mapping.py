import argparse
import json
import sys
import pysam


def get_fasta_chromosomes(fasta_file):
    """Extract chromosome names from FASTA file using pysam."""
    with pysam.FastaFile(fasta_file) as f:
        return f.references

def get_bam_chromosomes(bam_file):
    """Extract chromosome names from BAM file using pysam."""
    with pysam.AlignmentFile(bam_file, "rb") as bam:
        return list(bam.references)

def parse_extract_list(extract_arg):
    return [x.strip() for x in extract_arg.split(",") if x.strip()]

def create_chrom_mapping(fasta_files, bam_file, extract_list, name_list, output_json):
    bam_chroms = get_bam_chromosomes(bam_file)
    
    # Check that all chromosomes in extract_list are present in bam_chroms
    missing_in_bam = [chrom for chrom in extract_list if chrom not in bam_chroms]
    if missing_in_bam:
        print(f"Error: The following chromosomes are missing in BAM: {', '.join(missing_in_bam)}", file=sys.stderr)
        sys.exit(1)
    
    # Select the first fasta file that contains all chromosomes in extract_list
    selected_fasta = None
    for fasta_file in fasta_files:
        fasta_chroms = get_fasta_chromosomes(fasta_file)
        if all(chrom in fasta_chroms for chrom in extract_list):
            selected_fasta = fasta_file
            break
    
    if selected_fasta is None:
        print("Error: No FASTA file contains all chromosomes in extract_list.", file=sys.stderr)
        sys.exit(1)
     
    # Iterate over matching tuples and export the output_json
    mapping = []
    for chrom, name in zip(extract_list, name_list):
        row = {
            "fasta": chrom,
            "bam": chrom,
            "extract": True,
            "name": name
        }
        mapping.append(row)
    
    with open(output_json, "w") as f:
        json.dump({"reference": selected_fasta, "chromosomes": mapping}, f, indent=4)

def main():
    parser = argparse.ArgumentParser(description="Create chromosome mapping JSON.")
    parser.add_argument("--fastas", required=True, help="Comma-separated list of FASTA files")
    parser.add_argument("--bam", required=True, help="Path to BAM file")
    parser.add_argument("--extract", help='Comma-separated list of chromosome names to extract (as in BAM)')
    parser.add_argument("--name", required=True, help='"bam", "fasta", or path to file with exported names (one per line, order matches --extract)')
    parser.add_argument("--output", default="chrom_mapping.json", help="Output JSON file name")
    args = parser.parse_args()

    extract_list = args.extract and parse_extract_list(args.extract) or []
    create_chrom_mapping(args.fastas, args.bam, extract_list, args.name, args.output)

if __name__ == "__main__":
    main()
