"""
MINDEX Image Derivative Generator
==================================
Generates multiple size variants and WebP versions of images.

Sizes:
- thumb: 150x150 (square crop)
- small: 320px long edge
- medium: 640px long edge
- large: 1280px long edge
- original: untouched

All sizes also get WebP variants for web optimization.
"""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

# Derivative size definitions
DERIVATIVE_SIZES = {
    "thumb": (150, 150, True),    # (max_width, max_height, crop_square)
    "small": (320, 320, False),
    "medium": (640, 640, False),
    "large": (1280, 1280, False),
}

# Supported input formats
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# WebP quality settings
WEBP_QUALITY = 85
JPEG_QUALITY = 90


@dataclass
class DerivativeResult:
    """Result of derivative generation for one image."""
    original_path: str
    derivatives: Dict[str, str]  # size_name -> path
    webp_derivatives: Dict[str, str]  # size_name -> webp path
    success: bool
    error: Optional[str] = None


class ImageDerivativeGenerator:
    """
    Generates image derivatives (thumbnails, sizes, WebP variants).
    
    Usage:
        generator = ImageDerivativeGenerator(output_base="/path/to/derivatives")
        result = await generator.generate_all(original_path)
        print(result.derivatives)  # {'thumb': '...', 'small': '...', ...}
    """
    
    def __init__(self, output_base: Optional[Path] = None, executor: Optional[ThreadPoolExecutor] = None):
        """
        Args:
            output_base: Base directory for derivatives. If None, uses same dir as original.
            executor: Thread pool for parallel processing.
        """
        if Image is None:
            raise ImportError("Pillow is required: pip install Pillow")
        
        self.output_base = Path(output_base) if output_base else None
        self.executor = executor or ThreadPoolExecutor(max_workers=4)
    
    def _get_derivative_path(self, original_path: Path, size_name: str, format_ext: str) -> Path:
        """Generate path for a derivative."""
        if self.output_base:
            # Use structured output: output_base/size_name/original_stem.ext
            output_dir = self.output_base / size_name
        else:
            # Use same directory with size suffix
            output_dir = original_path.parent / "derivatives" / size_name
        
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{original_path.stem}.{format_ext}"
    
    def _resize_image(
        self,
        img: Image.Image,
        max_width: int,
        max_height: int,
        crop_square: bool = False,
    ) -> Image.Image:
        """Resize image maintaining aspect ratio or cropping to square."""
        if crop_square:
            # Square crop from center
            return ImageOps.fit(img, (max_width, max_height), method=Image.Resampling.LANCZOS)
        else:
            # Resize maintaining aspect ratio
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return img
    
    def _generate_derivative_sync(
        self,
        original_path: str,
        size_name: str,
        max_width: int,
        max_height: int,
        crop_square: bool,
    ) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        """
        Synchronous derivative generation (runs in thread pool).
        
        Returns: (size_name, jpg_path, webp_path, error)
        """
        try:
            path = Path(original_path)
            
            # Load image
            with Image.open(path) as img:
                # Convert to RGB if necessary (for JPEG output)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                
                # Resize
                resized = self._resize_image(img.copy(), max_width, max_height, crop_square)
                
                # Save JPEG derivative
                jpg_path = self._get_derivative_path(path, size_name, "jpg")
                resized.save(jpg_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
                
                # Save WebP derivative
                webp_path = self._get_derivative_path(path, f"{size_name}_webp", "webp")
                webp_path.parent.mkdir(parents=True, exist_ok=True)
                resized.save(webp_path, "WEBP", quality=WEBP_QUALITY, method=6)
                
                return (size_name, str(jpg_path), str(webp_path), None)
                
        except Exception as e:
            return (size_name, None, None, str(e))
    
    async def generate_derivative(
        self,
        original_path: str,
        size_name: str,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Generate a single derivative size.
        
        Returns: (jpg_path, webp_path, error)
        """
        if size_name not in DERIVATIVE_SIZES:
            return (None, None, f"Unknown size: {size_name}")
        
        max_width, max_height, crop_square = DERIVATIVE_SIZES[size_name]
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            self._generate_derivative_sync,
            original_path,
            size_name,
            max_width,
            max_height,
            crop_square,
        )
        
        _, jpg_path, webp_path, error = result
        return (jpg_path, webp_path, error)
    
    async def generate_all(
        self,
        original_path: str,
        sizes: Optional[List[str]] = None,
    ) -> DerivativeResult:
        """
        Generate all derivative sizes for an image.
        
        Args:
            original_path: Path to original image file.
            sizes: List of sizes to generate. If None, generates all.
        
        Returns:
            DerivativeResult with paths to all derivatives.
        """
        path = Path(original_path)
        
        # Validate input
        if not path.exists():
            return DerivativeResult(
                original_path=original_path,
                derivatives={},
                webp_derivatives={},
                success=False,
                error=f"File not found: {original_path}",
            )
        
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            return DerivativeResult(
                original_path=original_path,
                derivatives={},
                webp_derivatives={},
                success=False,
                error=f"Unsupported format: {path.suffix}",
            )
        
        # Determine sizes to generate
        target_sizes = sizes or list(DERIVATIVE_SIZES.keys())
        
        # Generate all derivatives in parallel
        tasks = [self.generate_derivative(original_path, size) for size in target_sizes]
        results = await asyncio.gather(*tasks)
        
        derivatives = {}
        webp_derivatives = {}
        errors = []
        
        for size_name, (jpg_path, webp_path, error) in zip(target_sizes, results):
            if error:
                errors.append(f"{size_name}: {error}")
            else:
                if jpg_path:
                    derivatives[size_name] = jpg_path
                if webp_path:
                    webp_derivatives[size_name] = webp_path
        
        return DerivativeResult(
            original_path=original_path,
            derivatives=derivatives,
            webp_derivatives=webp_derivatives,
            success=len(errors) == 0,
            error="; ".join(errors) if errors else None,
        )
    
    def get_dimensions(self, image_path: str) -> Tuple[int, int]:
        """Get image dimensions without loading full image into memory."""
        try:
            with Image.open(image_path) as img:
                return img.size  # (width, height)
        except Exception:
            return (0, 0)
    
    def meets_hq_threshold(self, image_path: str, min_long_edge: int = 1600) -> bool:
        """Check if image meets HQ threshold (default: 1600px on long edge)."""
        width, height = self.get_dimensions(image_path)
        return max(width, height) >= min_long_edge


async def generate_derivatives_for_image(
    image_path: str,
    output_base: Optional[str] = None,
) -> DerivativeResult:
    """
    Convenience function to generate all derivatives for an image.
    
    Args:
        image_path: Path to original image
        output_base: Optional base directory for derivatives
    
    Returns:
        DerivativeResult with all derivative paths
    """
    generator = ImageDerivativeGenerator(
        output_base=Path(output_base) if output_base else None
    )
    return await generator.generate_all(image_path)


if __name__ == "__main__":
    # Test the generator
    import sys
    
    async def test():
        if len(sys.argv) < 2:
            print("Usage: python derivatives.py <image_path>")
            return
        
        image_path = sys.argv[1]
        print(f"Generating derivatives for: {image_path}")
        
        result = await generate_derivatives_for_image(image_path)
        
        if result.success:
            print("\nDerivatives generated:")
            for size, path in result.derivatives.items():
                print(f"  {size}: {path}")
            print("\nWebP variants:")
            for size, path in result.webp_derivatives.items():
                print(f"  {size}: {path}")
        else:
            print(f"\nError: {result.error}")
    
    asyncio.run(test())
