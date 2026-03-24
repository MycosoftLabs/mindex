from fastapi import APIRouter, Query  # type: ignore
from typing import Optional

router = APIRouter(tags=["Genomes"])

GENOMES_MOCK = [
    {
        "id": "psilocybe_cubensis_v1",
        "species_name": "P. cubensis",
        "scientific_name": "Psilocybe cubensis",
        "assembly_name": "PsiCub_v1.0",
        "assembly_version": "1.0",
        "chromosome_count": 5,
        "total_length": 21600000,
        "gene_count": 8500,
        "source": "MINDEX",
        "chromosomes": [
            {"name": "chr1", "length": 5200000, "gc_content": 48.2},
            {"name": "chr2", "length": 4800000, "gc_content": 47.8},
            {"name": "chr3", "length": 4200000, "gc_content": 48.5},
            {"name": "chr4", "length": 3900000, "gc_content": 47.1},
            {"name": "chr5", "length": 3500000, "gc_content": 48.9},
        ]
    },
    {
        "id": "hericium_erinaceus_v1",
        "species_name": "H. erinaceus",
        "scientific_name": "Hericium erinaceus",
        "assembly_name": "HerEri_v1.0",
        "assembly_version": "1.0",
        "chromosome_count": 4,
        "total_length": 17100000,
        "gene_count": 7200,
        "source": "MINDEX",
        "chromosomes": [
            {"name": "chr1", "length": 4800000, "gc_content": 46.5},
            {"name": "chr2", "length": 4500000, "gc_content": 47.2},
            {"name": "chr3", "length": 4100000, "gc_content": 46.8},
            {"name": "chr4", "length": 3700000, "gc_content": 47.5},
        ]
    }
]

@router.get("/genomes")
async def get_genomes(species: Optional[str] = None):
    results = GENOMES_MOCK
    if species:
        results = [g for g in results if species.lower() in str(g.get("scientific_name", "")).lower()]
    return {"genomes": results}
