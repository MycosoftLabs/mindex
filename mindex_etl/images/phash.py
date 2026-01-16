"""
MINDEX Perceptual Hash (pHash) Module
=======================================
Computes perceptual hashes for image deduplication and similarity detection.

Features:
- SHA-256 exact deduplication
- pHash perceptual hashing
- Hamming distance for near-duplicate detection
- Batch processing support
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import imagehash
except ImportError:
    imagehash = None


# Default threshold for near-duplicate detection
DEFAULT_HAMMING_THRESHOLD = 6


@dataclass
class HashResult:
    """Result of hash computation for an image."""
    file_path: str
    sha256: Optional[str] = None
    phash: Optional[str] = None
    dhash: Optional[str] = None
    ahash: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


class ImageHasher:
    """
    Computes various hashes for images.
    
    Usage:
        hasher = ImageHasher()
        result = hasher.compute_hashes("/path/to/image.jpg")
        print(f"SHA-256: {result.sha256}")
        print(f"pHash: {result.phash}")
        
        # Check for near-duplicates
        is_similar = hasher.is_near_duplicate(phash1, phash2, threshold=6)
    """
    
    def __init__(self, hash_size: int = 16):
        """
        Args:
            hash_size: Size of perceptual hash (default 16 = 64-bit hash)
        """
        if imagehash is None:
            raise ImportError("imagehash is required: pip install imagehash")
        if Image is None:
            raise ImportError("Pillow is required: pip install Pillow")
        
        self.hash_size = hash_size
    
    def compute_sha256(self, file_path: str) -> Optional[str]:
        """Compute SHA-256 hash of file contents."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return None
    
    def compute_sha256_from_bytes(self, data: bytes) -> str:
        """Compute SHA-256 hash from bytes."""
        return hashlib.sha256(data).hexdigest()
    
    def compute_phash(self, file_path: str) -> Optional[str]:
        """
        Compute perceptual hash (pHash).
        
        pHash is resistant to:
        - Scaling
        - Aspect ratio changes
        - Minor color adjustments
        - JPEG compression
        """
        try:
            with Image.open(file_path) as img:
                return str(imagehash.phash(img, hash_size=self.hash_size))
        except Exception:
            return None
    
    def compute_dhash(self, file_path: str) -> Optional[str]:
        """
        Compute difference hash (dHash).
        
        dHash is faster and good for detecting:
        - Cropping
        - Minor edits
        """
        try:
            with Image.open(file_path) as img:
                return str(imagehash.dhash(img, hash_size=self.hash_size))
        except Exception:
            return None
    
    def compute_ahash(self, file_path: str) -> Optional[str]:
        """
        Compute average hash (aHash).
        
        aHash is the simplest but least robust.
        """
        try:
            with Image.open(file_path) as img:
                return str(imagehash.average_hash(img, hash_size=self.hash_size))
        except Exception:
            return None
    
    def compute_hashes(
        self,
        file_path: str,
        include_dhash: bool = False,
        include_ahash: bool = False,
    ) -> HashResult:
        """
        Compute all requested hashes for an image.
        
        Args:
            file_path: Path to image file
            include_dhash: Whether to compute dHash
            include_ahash: Whether to compute aHash
        
        Returns:
            HashResult with all computed hashes
        """
        path = Path(file_path)
        
        if not path.exists():
            return HashResult(
                file_path=file_path,
                success=False,
                error=f"File not found: {file_path}",
            )
        
        try:
            result = HashResult(file_path=file_path)
            
            # SHA-256 (always computed)
            result.sha256 = self.compute_sha256(file_path)
            
            # pHash (always computed)
            result.phash = self.compute_phash(file_path)
            
            # Optional hashes
            if include_dhash:
                result.dhash = self.compute_dhash(file_path)
            if include_ahash:
                result.ahash = self.compute_ahash(file_path)
            
            return result
            
        except Exception as e:
            return HashResult(
                file_path=file_path,
                success=False,
                error=str(e),
            )
    
    @staticmethod
    def hamming_distance(hash1: str, hash2: str) -> int:
        """
        Compute Hamming distance between two hex hash strings.
        
        Lower distance = more similar images.
        - 0: Identical
        - 1-5: Very similar (likely same image with minor changes)
        - 6-10: Similar (possibly same subject, different photo)
        - 10+: Different images
        """
        if len(hash1) != len(hash2):
            # Convert to same length for comparison
            min_len = min(len(hash1), len(hash2))
            hash1 = hash1[:min_len]
            hash2 = hash2[:min_len]
        
        # Convert hex to binary and count differences
        try:
            int1 = int(hash1, 16)
            int2 = int(hash2, 16)
            xor = int1 ^ int2
            return bin(xor).count("1")
        except ValueError:
            return 999  # Invalid hash
    
    def is_near_duplicate(
        self,
        phash1: str,
        phash2: str,
        threshold: int = DEFAULT_HAMMING_THRESHOLD,
    ) -> bool:
        """
        Check if two pHashes indicate near-duplicate images.
        
        Args:
            phash1: First perceptual hash
            phash2: Second perceptual hash
            threshold: Maximum Hamming distance (default: 6)
        
        Returns:
            True if images are near-duplicates
        """
        distance = self.hamming_distance(phash1, phash2)
        return distance <= threshold
    
    def find_near_duplicates(
        self,
        target_phash: str,
        phash_list: List[Tuple[str, str]],  # [(id, phash), ...]
        threshold: int = DEFAULT_HAMMING_THRESHOLD,
    ) -> List[Tuple[str, int]]:
        """
        Find all near-duplicates of a target hash in a list.
        
        Args:
            target_phash: pHash to search for
            phash_list: List of (id, phash) tuples to search
            threshold: Maximum Hamming distance
        
        Returns:
            List of (id, distance) for all matches
        """
        matches = []
        for id_, phash in phash_list:
            distance = self.hamming_distance(target_phash, phash)
            if distance <= threshold:
                matches.append((id_, distance))
        
        return sorted(matches, key=lambda x: x[1])


def compute_image_hashes(file_path: str) -> HashResult:
    """
    Convenience function to compute hashes for an image.
    
    Returns:
        HashResult with sha256 and phash
    """
    hasher = ImageHasher()
    return hasher.compute_hashes(file_path)


def check_duplicate(sha256: str, existing_hashes: set) -> bool:
    """Check if SHA-256 indicates exact duplicate."""
    return sha256 in existing_hashes


def check_near_duplicate(
    phash: str,
    existing_phashes: List[str],
    threshold: int = DEFAULT_HAMMING_THRESHOLD,
) -> Optional[str]:
    """
    Check if pHash indicates near-duplicate.
    
    Returns:
        The matching pHash if found, None otherwise
    """
    hasher = ImageHasher()
    for existing in existing_phashes:
        if hasher.is_near_duplicate(phash, existing, threshold):
            return existing
    return None


# SQL helper for PostgreSQL
def get_near_duplicate_query(threshold: int = DEFAULT_HAMMING_THRESHOLD) -> str:
    """
    Generate SQL query for finding near-duplicates in PostgreSQL.
    
    Note: Requires a custom hamming_distance function in PostgreSQL.
    """
    return f"""
    -- Create function if not exists
    CREATE OR REPLACE FUNCTION hamming_distance(hash1 text, hash2 text)
    RETURNS integer AS $$
    DECLARE
        i integer;
        distance integer := 0;
        len integer;
        c1 char;
        c2 char;
        v1 integer;
        v2 integer;
    BEGIN
        len := LEAST(length(hash1), length(hash2));
        FOR i IN 1..len LOOP
            c1 := substring(hash1 from i for 1);
            c2 := substring(hash2 from i for 1);
            v1 := ('x' || c1)::bit(4)::integer;
            v2 := ('x' || c2)::bit(4)::integer;
            -- Count differing bits
            distance := distance + (
                SELECT count(*) FROM generate_series(0, 3) AS b
                WHERE ((v1 >> b) & 1) != ((v2 >> b) & 1)
            );
        END LOOP;
        RETURN distance;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE;

    -- Find near-duplicates for a given hash
    SELECT id, perceptual_hash, hamming_distance(perceptual_hash, :target_hash) as distance
    FROM media.image
    WHERE perceptual_hash IS NOT NULL
      AND hamming_distance(perceptual_hash, :target_hash) <= {threshold}
    ORDER BY distance;
    """


if __name__ == "__main__":
    # Test the hasher
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python phash.py <image_path> [<compare_image_path>]")
        sys.exit(1)
    
    image_path = sys.argv[1]
    print(f"Computing hashes for: {image_path}")
    
    result = compute_image_hashes(image_path)
    
    if result.success:
        print(f"\nSHA-256: {result.sha256}")
        print(f"pHash:   {result.phash}")
        
        # Compare with second image if provided
        if len(sys.argv) > 2:
            compare_path = sys.argv[2]
            print(f"\nComparing with: {compare_path}")
            
            result2 = compute_image_hashes(compare_path)
            if result2.success and result.phash and result2.phash:
                hasher = ImageHasher()
                distance = hasher.hamming_distance(result.phash, result2.phash)
                is_dup = hasher.is_near_duplicate(result.phash, result2.phash)
                
                print(f"Hamming distance: {distance}")
                print(f"Near-duplicate: {is_dup}")
    else:
        print(f"Error: {result.error}")
