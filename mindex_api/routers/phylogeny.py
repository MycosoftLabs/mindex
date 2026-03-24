from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(tags=["Phylogeny"])

@router.get("/phylogeny")
async def get_phylogeny(clade: Optional[str] = None):
    """Get the taxonomic tree for a given clade."""
    # Build a simplified mock tree for the frontend
    tree = {
        "id": "root",
        "name": "Fungi",
        "scientific_name": "Fungi",
        "rank": "kingdom",
        "parent_id": None,
        "children": [
            {
                "id": "basidiomycota",
                "name": "Basidiomycetes",
                "scientific_name": "Basidiomycota",
                "rank": "phylum",
                "parent_id": "root",
                "children": [
                    {
                        "id": "agaricomycetes",
                        "name": "Agaricomycetes",
                        "scientific_name": "Agaricomycetes",
                        "rank": "class",
                        "parent_id": "basidiomycota",
                    }
                ]
            }
        ]
    }
    return {"success": True, "tree": tree}
