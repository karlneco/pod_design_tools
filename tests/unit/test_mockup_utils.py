"""
Unit tests for mockup generation utilities.
"""
import pytest
from pathlib import Path
from PIL import Image

from app.utils.mockups import generate_mockups_for_design


@pytest.mark.unit
class TestMockupGeneration:
    """Tests for mockup generation utilities."""

    def test_generate_mockup_basic(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test basic mockup generation with default placement."""
        out_dir = tmp_path / "mockups"

        placements = {
            "center": {
                "x": 100,
                "y": 100,
                "max_w": 80,
                "max_h": 80
            }
        }

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert len(result) == 1
        assert result[0].exists()
        assert result[0].name == "mockup_mockup_template.png"

        # Verify output is a valid image
        img = Image.open(result[0])
        assert img.size == (200, 200)
        assert img.mode == "RGB"

    def test_generate_mockup_multiple_templates(self, tmp_path, sample_design_image):
        """Test generating mockups for multiple templates."""
        # Create multiple template images
        template1 = tmp_path / "template1.png"
        template2 = tmp_path / "template2.png"

        Image.new('RGB', (200, 200), (255, 255, 255)).save(template1)
        Image.new('RGB', (300, 300), (200, 200, 200)).save(template2)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(template1), str(template2)],
            placements=placements,
            out_dir=out_dir
        )

        assert len(result) == 2
        assert result[0].name == "mockup_template1.png"
        assert result[1].name == "mockup_template2.png"

    def test_generate_mockup_creates_output_directory(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test that generate_mockups_for_design creates the output directory."""
        out_dir = tmp_path / "nested" / "mockups" / "output"
        assert not out_dir.exists()

        placements = {"center": {"x": 100, "y": 100, "max_w": 50, "max_h": 50}}

        generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert out_dir.exists()
        assert out_dir.is_dir()

    def test_generate_mockup_with_custom_scale(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test mockup generation with custom scale factor."""
        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir,
            scale=0.5  # Half size
        )

        assert len(result) == 1
        assert result[0].exists()

    def test_generate_mockup_preserves_aspect_ratio(self, tmp_path, sample_mockup_template):
        """Test that mockup generation preserves design aspect ratio."""
        # Create a rectangular design (wider than tall)
        design = tmp_path / "wide_design.png"
        Image.new('RGBA', (200, 100), (255, 0, 0, 255)).save(design)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(design),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        # The output should exist and be valid
        assert result[0].exists()

        # Load the output and verify it's composited correctly
        img = Image.open(result[0])
        assert img.mode == "RGB"

    def test_generate_mockup_with_transparency(self, tmp_path, sample_mockup_template):
        """Test mockup generation with transparent design."""
        # Create a design with transparency
        design = tmp_path / "transparent_design.png"
        img = Image.new('RGBA', (100, 100), (255, 0, 0, 0))  # Fully transparent
        # Add a semi-transparent red square
        for x in range(25, 75):
            for y in range(25, 75):
                img.putpixel((x, y), (255, 0, 0, 128))
        img.save(design)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(design),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert result[0].exists()

        # Verify the output is correctly composited
        output_img = Image.open(result[0])
        assert output_img.mode == "RGB"

    def test_generate_mockup_default_placement(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test mockup generation with default placement when key not found."""
        out_dir = tmp_path / "mockups"

        # Empty placements dict - should use default centered placement
        placements = {}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert len(result) == 1
        assert result[0].exists()

    def test_generate_mockup_large_design(self, tmp_path, sample_mockup_template):
        """Test mockup generation with a design larger than constraints."""
        # Create a large design
        large_design = tmp_path / "large_design.png"
        Image.new('RGBA', (500, 500), (0, 255, 0, 255)).save(large_design)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(large_design),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert result[0].exists()

        # The design should be scaled down to fit within constraints
        # The output template is still 200x200
        output_img = Image.open(result[0])
        assert output_img.size == (200, 200)

    def test_generate_mockup_small_design(self, tmp_path, sample_mockup_template):
        """Test mockup generation with a design smaller than constraints."""
        # Create a small design
        small_design = tmp_path / "small_design.png"
        Image.new('RGBA', (20, 20), (0, 0, 255, 255)).save(small_design)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(small_design),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert result[0].exists()

        # The design should be scaled up to fit better
        output_img = Image.open(result[0])
        assert output_img.size == (200, 200)

    def test_generate_mockup_off_center_placement(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test mockup generation with off-center placement."""
        out_dir = tmp_path / "mockups"

        # Place design in top-left corner
        placements = {
            "center": {
                "x": 50,
                "y": 50,
                "max_w": 60,
                "max_h": 60
            }
        }

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        assert result[0].exists()

    def test_generate_mockup_output_optimized(self, tmp_path, sample_design_image, sample_mockup_template):
        """Test that mockup outputs are optimized PNGs."""
        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(sample_mockup_template)],
            placements=placements,
            out_dir=out_dir
        )

        # Verify the output file is a PNG
        assert result[0].suffix == ".png"

        # Verify it can be opened as an image
        img = Image.open(result[0])
        assert img.format == "PNG"

    def test_generate_mockup_returns_paths_list(self, tmp_path, sample_design_image):
        """Test that generate_mockups_for_design returns a list of Path objects."""
        template1 = tmp_path / "t1.png"
        template2 = tmp_path / "t2.png"
        Image.new('RGB', (200, 200), (255, 255, 255)).save(template1)
        Image.new('RGB', (200, 200), (255, 255, 255)).save(template2)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(template1), str(template2)],
            placements=placements,
            out_dir=out_dir
        )

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(p, Path) for p in result)
        assert all(p.exists() for p in result)

    def test_generate_mockup_with_different_template_sizes(self, tmp_path, sample_design_image):
        """Test mockup generation with templates of different sizes."""
        template_small = tmp_path / "small.png"
        template_large = tmp_path / "large.png"

        Image.new('RGB', (150, 150), (255, 255, 255)).save(template_small)
        Image.new('RGB', (400, 400), (200, 200, 200)).save(template_large)

        out_dir = tmp_path / "mockups"
        placements = {"center": {"x": 100, "y": 100, "max_w": 80, "max_h": 80}}

        result = generate_mockups_for_design(
            design_png_path=str(sample_design_image),
            templates=[str(template_small), str(template_large)],
            placements=placements,
            out_dir=out_dir
        )

        assert len(result) == 2

        # Verify both mockups have different sizes matching templates
        img_small = Image.open(result[0])
        img_large = Image.open(result[1])

        assert img_small.size == (150, 150)
        assert img_large.size == (400, 400)
