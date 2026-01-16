"""
MINDEX Image Quality Scoring Module
=====================================
Computes quality scores for images based on multiple factors:
- Resolution (long edge)
- Sharpness/blur detection
- Noise/compression artifact estimation
- Color vibrancy

Quality Score: 0-100
- 70+: HQ (suitable for training)
- 50-69: Acceptable
- <50: Low quality
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image
    import numpy as np
except ImportError:
    Image = None
    np = None


# Thresholds
MIN_HQ_LONG_EDGE = 1600  # Minimum for HQ classification
IDEAL_LONG_EDGE = 2400   # Optimal resolution
MAX_RESOLUTION_SCORE = 30
MAX_SHARPNESS_SCORE = 30
MAX_NOISE_SCORE = 20
MAX_COLOR_SCORE = 20


@dataclass
class QualityResult:
    """Result of quality analysis for an image."""
    file_path: str
    width: int = 0
    height: int = 0
    long_edge: int = 0
    
    # Component scores (0-100 normalized)
    resolution_score: float = 0.0
    sharpness_score: float = 0.0
    noise_score: float = 0.0
    color_score: float = 0.0
    
    # Final score (0-100)
    quality_score: float = 0.0
    
    # Classification
    is_hq: bool = False
    meets_min_resolution: bool = False
    
    success: bool = True
    error: Optional[str] = None


class ImageQualityAnalyzer:
    """
    Analyzes image quality for training suitability.
    
    Usage:
        analyzer = ImageQualityAnalyzer()
        result = analyzer.analyze("/path/to/image.jpg")
        print(f"Quality: {result.quality_score:.1f}/100")
        print(f"HQ: {result.is_hq}")
    """
    
    def __init__(
        self,
        min_hq_long_edge: int = MIN_HQ_LONG_EDGE,
        hq_score_threshold: float = 70.0,
    ):
        """
        Args:
            min_hq_long_edge: Minimum long edge for HQ classification
            hq_score_threshold: Minimum score for HQ classification
        """
        if Image is None or np is None:
            raise ImportError("Pillow and numpy are required: pip install Pillow numpy")
        
        self.min_hq_long_edge = min_hq_long_edge
        self.hq_score_threshold = hq_score_threshold
    
    def _compute_resolution_score(self, long_edge: int) -> float:
        """
        Compute resolution score based on long edge.
        
        Returns: 0-100 score
        """
        if long_edge >= IDEAL_LONG_EDGE:
            return 100.0
        elif long_edge >= MIN_HQ_LONG_EDGE:
            # Linear interpolation between MIN and IDEAL
            return 70.0 + (long_edge - MIN_HQ_LONG_EDGE) / (IDEAL_LONG_EDGE - MIN_HQ_LONG_EDGE) * 30.0
        elif long_edge >= 800:
            # Below HQ threshold but acceptable
            return 40.0 + (long_edge - 800) / (MIN_HQ_LONG_EDGE - 800) * 30.0
        else:
            # Low resolution
            return max(0, long_edge / 800 * 40.0)
    
    def _compute_sharpness_score(self, img_array: np.ndarray) -> float:
        """
        Compute sharpness score using Laplacian variance.
        
        Higher variance = sharper image.
        Returns: 0-100 score
        """
        try:
            # Convert to grayscale if needed
            if len(img_array.shape) == 3:
                gray = np.mean(img_array, axis=2).astype(np.float32)
            else:
                gray = img_array.astype(np.float32)
            
            # Laplacian kernel for edge detection
            kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
            
            # Simple convolution for Laplacian
            from scipy import ndimage
            laplacian = ndimage.convolve(gray, kernel)
            variance = laplacian.var()
            
            # Normalize variance to 0-100 score
            # Typical variance ranges: 100 (blurry) to 3000+ (very sharp)
            if variance < 100:
                return 20.0
            elif variance < 500:
                return 20.0 + (variance - 100) / 400 * 30.0
            elif variance < 1500:
                return 50.0 + (variance - 500) / 1000 * 30.0
            else:
                return min(100.0, 80.0 + (variance - 1500) / 1500 * 20.0)
                
        except ImportError:
            # Fallback without scipy: use standard deviation as rough estimate
            if len(img_array.shape) == 3:
                gray = np.mean(img_array, axis=2)
            else:
                gray = img_array
            
            # High frequency content estimation
            dx = np.diff(gray, axis=1)
            dy = np.diff(gray, axis=0)
            gradient_mag = np.sqrt(np.mean(dx**2) + np.mean(dy**2))
            
            # Normalize to 0-100
            return min(100.0, gradient_mag / 50 * 100)
        except Exception:
            return 50.0  # Default if analysis fails
    
    def _compute_noise_score(self, img_array: np.ndarray) -> float:
        """
        Estimate noise level (lower noise = higher score).
        
        Uses local variance in smooth regions.
        Returns: 0-100 score (100 = no noise)
        """
        try:
            if len(img_array.shape) == 3:
                gray = np.mean(img_array, axis=2).astype(np.float32)
            else:
                gray = img_array.astype(np.float32)
            
            # Estimate noise using median absolute deviation
            # of Laplacian in smooth regions
            from scipy import ndimage
            kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
            laplacian = ndimage.convolve(gray, kernel)
            
            # MAD of Laplacian is proportional to noise
            mad = np.median(np.abs(laplacian - np.median(laplacian)))
            noise_estimate = mad * 1.4826  # Convert MAD to std estimate
            
            # Convert to score (lower noise = higher score)
            if noise_estimate < 5:
                return 100.0
            elif noise_estimate < 15:
                return 80.0 - (noise_estimate - 5) / 10 * 20.0
            elif noise_estimate < 30:
                return 60.0 - (noise_estimate - 15) / 15 * 20.0
            else:
                return max(20.0, 40.0 - (noise_estimate - 30) / 30 * 20.0)
                
        except ImportError:
            # Fallback: use simple variance in small patches
            h, w = img_array.shape[:2]
            patch_size = 16
            variances = []
            
            for i in range(0, h - patch_size, patch_size):
                for j in range(0, w - patch_size, patch_size):
                    patch = img_array[i:i+patch_size, j:j+patch_size]
                    if len(patch.shape) == 3:
                        patch = np.mean(patch, axis=2)
                    variances.append(np.var(patch))
            
            # Lower median variance in smooth areas = less noise
            median_var = np.median(variances) if variances else 100
            return max(20.0, 100.0 - median_var / 50 * 80.0)
        except Exception:
            return 70.0  # Default if analysis fails
    
    def _compute_color_score(self, img_array: np.ndarray) -> float:
        """
        Compute color vibrancy score.
        
        Higher saturation and good exposure = higher score.
        Returns: 0-100 score
        """
        try:
            if len(img_array.shape) != 3:
                return 50.0  # Grayscale
            
            # Convert to HSV-like values for saturation analysis
            r, g, b = img_array[:,:,0], img_array[:,:,1], img_array[:,:,2]
            max_c = np.maximum(np.maximum(r, g), b)
            min_c = np.minimum(np.minimum(r, g), b)
            
            # Saturation
            delta = max_c - min_c
            saturation = np.where(max_c != 0, delta / max_c, 0)
            mean_saturation = np.mean(saturation)
            
            # Value (brightness)
            mean_value = np.mean(max_c) / 255.0
            
            # Ideal is moderately saturated and well-exposed
            # Penalize very low or very high brightness
            exposure_factor = 1.0 - abs(mean_value - 0.5) * 2
            exposure_factor = max(0.3, min(1.0, exposure_factor))
            
            # Saturation score (0.2-0.6 is ideal for natural photos)
            if mean_saturation < 0.1:
                sat_score = 40.0
            elif mean_saturation < 0.3:
                sat_score = 60.0 + (mean_saturation - 0.1) / 0.2 * 30.0
            elif mean_saturation < 0.6:
                sat_score = 90.0
            else:
                sat_score = 90.0 - (mean_saturation - 0.6) / 0.4 * 30.0
            
            return sat_score * exposure_factor
            
        except Exception:
            return 60.0  # Default if analysis fails
    
    def analyze(self, file_path: str) -> QualityResult:
        """
        Analyze image quality.
        
        Args:
            file_path: Path to image file
        
        Returns:
            QualityResult with all scores and classification
        """
        path = Path(file_path)
        
        if not path.exists():
            return QualityResult(
                file_path=file_path,
                success=False,
                error=f"File not found: {file_path}",
            )
        
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                long_edge = max(width, height)
                
                # Convert to numpy array for analysis
                img_array = np.array(img.convert("RGB"))
                
                # Compute component scores
                resolution_score = self._compute_resolution_score(long_edge)
                sharpness_score = self._compute_sharpness_score(img_array)
                noise_score = self._compute_noise_score(img_array)
                color_score = self._compute_color_score(img_array)
                
                # Weighted final score
                # Resolution: 30%, Sharpness: 30%, Noise: 20%, Color: 20%
                quality_score = (
                    resolution_score * 0.30 +
                    sharpness_score * 0.30 +
                    noise_score * 0.20 +
                    color_score * 0.20
                )
                
                # Classification
                meets_min_resolution = long_edge >= self.min_hq_long_edge
                is_hq = quality_score >= self.hq_score_threshold and meets_min_resolution
                
                return QualityResult(
                    file_path=file_path,
                    width=width,
                    height=height,
                    long_edge=long_edge,
                    resolution_score=round(resolution_score, 1),
                    sharpness_score=round(sharpness_score, 1),
                    noise_score=round(noise_score, 1),
                    color_score=round(color_score, 1),
                    quality_score=round(quality_score, 1),
                    is_hq=is_hq,
                    meets_min_resolution=meets_min_resolution,
                    success=True,
                )
                
        except Exception as e:
            return QualityResult(
                file_path=file_path,
                success=False,
                error=str(e),
            )
    
    def quick_check(self, file_path: str) -> Tuple[bool, int]:
        """
        Quick resolution check without full analysis.
        
        Returns: (meets_min_resolution, long_edge)
        """
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                long_edge = max(width, height)
                return (long_edge >= self.min_hq_long_edge, long_edge)
        except Exception:
            return (False, 0)


def analyze_image_quality(
    file_path: str,
    min_hq_long_edge: int = MIN_HQ_LONG_EDGE,
) -> QualityResult:
    """
    Convenience function to analyze image quality.
    
    Args:
        file_path: Path to image file
        min_hq_long_edge: Minimum long edge for HQ classification
    
    Returns:
        QualityResult with all scores
    """
    analyzer = ImageQualityAnalyzer(min_hq_long_edge=min_hq_long_edge)
    return analyzer.analyze(file_path)


def is_hq_image(file_path: str, min_long_edge: int = MIN_HQ_LONG_EDGE) -> bool:
    """Quick check if image meets HQ criteria."""
    analyzer = ImageQualityAnalyzer(min_hq_long_edge=min_long_edge)
    meets_min, _ = analyzer.quick_check(file_path)
    return meets_min


if __name__ == "__main__":
    # Test the analyzer
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python quality.py <image_path>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    print(f"Analyzing: {image_path}\n")
    
    result = analyze_image_quality(image_path)
    
    if result.success:
        print(f"Dimensions: {result.width}x{result.height}")
        print(f"Long Edge: {result.long_edge}px")
        print()
        print("Component Scores:")
        print(f"  Resolution: {result.resolution_score:.1f}/100")
        print(f"  Sharpness:  {result.sharpness_score:.1f}/100")
        print(f"  Noise:      {result.noise_score:.1f}/100")
        print(f"  Color:      {result.color_score:.1f}/100")
        print()
        print(f"Final Score: {result.quality_score:.1f}/100")
        print(f"Classification: {'HQ ✓' if result.is_hq else 'Standard'}")
        print(f"Meets Min Resolution: {'Yes' if result.meets_min_resolution else 'No'}")
    else:
        print(f"Error: {result.error}")
