from PIL import Image, ImageDraw, ImageFont
import os
import sys

# --- Ensure the 'assets' directory exists ---
# This line will create the 'assets' folder in the same directory
# where generate_icon.py is executed, if it doesn't already exist.
# exist_ok=True prevents an error if the directory is already there.
ASSETS_DIR = "assets"
os.makedirs(ASSETS_DIR, exist_ok=True)
print(f"Ensured '{ASSETS_DIR}/' directory exists.")

def create_simple_icon(output_filename, size=(256, 256), bg_color=(0, 0, 0, 0), text="", text_color=(255, 255, 255), font_path=None, font_size=None, shape=None, shape_color=(255, 0, 0)):
    """
    Generates a simple icon image with an optional background, text, and shape,
    and saves it into the 'assets' directory.

    Args:
        output_filename (str): The name of the PNG icon file (e.g., "icon.png").
                               It will be saved inside the 'assets' directory.
        size (tuple): The dimensions of the icon (width, height). Default is 256x256.
        bg_color (tuple): RGBA tuple for the background color. Default is transparent black.
        text (str): The text to draw on the icon.
        text_color (tuple): RGB tuple for the text color.
        font_path (str): Path to a TrueType font file (.ttf). If None, Pillow tries a default.
        font_size (int): The size of the font. If None, it's auto-calculated based on icon size.
        shape (str): 'circle', 'square', or None. Draws a shape in the center.
        shape_color (tuple): RGB tuple for the shape color.
    """
    output_path = os.path.join(ASSETS_DIR, output_filename)

    # Create a new image with a transparent background
    img = Image.new('RGBA', size, bg_color)
    draw = ImageDraw.Draw(img)

    center_x, center_y = size[0] // 2, size[1] // 2

    # Draw shape if specified
    if shape:
        shape_padding = size[0] // 8 # Padding from edges
        if shape == 'circle':
            radius = min(size) // 2 - shape_padding
            draw.ellipse((center_x - radius, center_y - radius,
                          center_x + radius, center_y + radius),
                         fill=shape_color)
        elif shape == 'square':
            side = min(size) - 2 * shape_padding
            draw.rectangle((center_x - side // 2, center_y - side // 2,
                            center_x + side // 2, center_y + side // 2),
                           fill=shape_color)

    # Load font or use default
    font = None
    if font_path and os.path.exists(font_path):
        try:
            font = ImageFont.truetype(font_path, font_size if font_size else int(size[1] * 0.5))
        except IOError:
            print(f"Warning: Could not load font from '{font_path}'. Using a default font. Check font file permissions or path.")
    
    if font is None: # Fallback to a system-dependent default or Pillow's internal default
        try:
            # Attempt to use a common system font if no font_path or it failed
            if sys.platform.startswith('win'):
                # Windows paths
                default_font_candidate = "C:/Windows/Fonts/arial.ttf"
            elif sys.platform.startswith('darwin'):
                # macOS paths
                default_font_candidate = "/System/Library/Fonts/Arial.ttf"
            else: # Linux
                # Common Linux font paths, try a few
                default_font_candidate = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                if not os.path.exists(default_font_candidate):
                    default_font_candidate = "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"
                if not os.path.exists(default_font_candidate):
                    default_font_candidate = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"

            if os.path.exists(default_font_candidate):
                font = ImageFont.truetype(default_font_candidate, font_size if font_size else int(size[1] * 0.5))
                print(f"Info: Using system default font: '{default_font_candidate}'")
            else:
                # Last resort: Pillow's internal default font
                font = ImageFont.load_default()
                print("Warning: No specified font or common system font found. Using Pillow's basic default font.")
        except Exception as e:
            print(f"Error trying to load default font: {e}. Using basic Pillow default.")
            font = ImageFont.load_default()

    # Draw text if specified
    if text and font: # Only draw if text and a font are successfully loaded
        # Calculate text bounding box to center it
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        text_x = center_x - text_width // 2
        text_y = center_y - text_height // 2
        draw.text((text_x, text_y), text, fill=text_color, font=font)

    # Save the image
    img.save(output_path)
    print(f"Icon created successfully at: {output_path}")

if __name__ == "__main__":
    # --- Example 1: Simple Red "M" icon ---
    print("\n--- Generating 'M' icon ---")
    create_simple_icon(
        output_filename="icon_M.png",
        size=(256, 256),
        bg_color=(255, 0, 0, 255),  # Solid Red
        text="M",
        text_color=(255, 255, 255), # White text
        font_size=180,
        # font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" # Uncomment and adjust if you have a specific font
    )

    # --- Example 2: Blue Circle with "X" icon ---
    print("\n--- Generating 'X' icon ---")
    create_simple_icon(
        output_filename="icon_X.png",
        size=(256, 256),
        bg_color=(0, 0, 0, 0),  # Transparent background
        shape='circle',
        shape_color=(0, 0, 255), # Blue circle
        text="X",
        text_color=(255, 255, 255), # White text
        font_size=160,
        # font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    )

    # --- Example 3: MiFlashX inspired icon (more complex, multi-text) ---
    print("\n--- Generating 'MiFlashX' app icon ---")
    icon_size = 256
    img_miflashx = Image.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0)) # Transparent background
    draw_miflashx = ImageDraw.Draw(img_miflashx)

    # Define colors
    mi_color = (255, 103, 0, 255)  # Xiaomi Orange (approx)
    flash_color = (200, 200, 200, 255) # Light grey
    x_color = (255, 255, 255, 255) # White

    # Define common font paths for various OS, will try to find one
    font_paths_bold = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", # Linux
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf", # Linux
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", # Linux
        "/System/Library/Fonts/Arial Bold.ttf", # macOS
        "C:/Windows/Fonts/arialbd.ttf" # Windows
    ]
    font_paths_regular = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", # Linux
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf", # Linux
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf", # Linux
        "/System/Library/Fonts/Arial.ttf", # macOS
        "C:/Windows/Fonts/arial.ttf" # Windows
    ]

    def get_first_available_font(paths, default_size):
        for path in paths:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, default_size)
                except IOError:
                    continue # Try next path if this one is problematic
        print(f"Warning: No suitable font found from candidates. Using basic default for {paths[0]}.")
        return ImageFont.load_default() # Fallback

    # Load fonts
    font_mi = get_first_available_font(font_paths_bold, int(icon_size * 0.4))
    font_flash = get_first_available_font(font_paths_regular, int(icon_size * 0.2))
    font_x = get_first_available_font(font_paths_bold, int(icon_size * 0.4))

    # Draw background circle (optional, for a rounded look)
    # This provides a dark grey circular base for the icon
    draw_miflashx.ellipse((0, 0, icon_size, icon_size), fill=(50, 50, 50, 255))

    # "Mi" text
    text_mi = "Mi"
    bbox_mi = draw_miflashx.textbbox((0, 0), text_mi, font=font_mi)
    text_mi_width = bbox_mi[2] - bbox_mi[0]
    # Positioning: 15% from left, 20% from top
    draw_miflashx.text((icon_size * 0.15, icon_size * 0.2), text_mi, fill=mi_color, font=font_mi)

    # "Flash" text
    text_flash = "Flash"
    bbox_flash = draw_miflashx.textbbox((0, 0), text_flash, font=font_flash)
    # Positioning: 15% from left, 50% from top
    draw_miflashx.text((icon_size * 0.15, icon_size * 0.5), text_flash, fill=flash_color, font=font_flash)

    # "X" text (aligned to the right)
    text_x = "X"
    bbox_x = draw_miflashx.textbbox((0, 0), text_x, font=font_x)
    text_x_width = bbox_x[2] - bbox_x[0]
    # Positioning: 15% from right (icon_size - text_width - 15%), 35% from top
    draw_miflashx.text((icon_size - text_x_width - icon_size * 0.15, icon_size * 0.35), text_x, fill=x_color, font=font_x)

    img_miflashx.save(os.path.join(ASSETS_DIR, "icon_MiFlashX.png"))
    print(f"Icon created successfully at: {os.path.join(ASSETS_DIR, 'icon_MiFlashX.png')}")

    # The default "icon.png" for the project, using a simple design for direct use
    print("\n--- Generating default 'icon.png' for MiFlashX project ---")
    create_simple_icon(
        output_filename="icon.png", # This is the file name used by the build script
        size=(256, 256),
        bg_color=(20, 20, 20, 255), # Dark grey background
        text="MI",
        text_color=(255, 103, 0, 255), # Xiaomi Orange
        font_size=150,
        # font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" # Will use auto-detection if commented
    )