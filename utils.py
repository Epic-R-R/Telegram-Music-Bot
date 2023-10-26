import requests

def telegram_html_escape(string: str):
    return string.replace("<", "&lt;") \
        .replace(">", "&gt;") \
        .replace("&", "&amp;") \
        .replace('"', "&quot;")


def check_thumbnail(thumbnail_url):
    try:
        response = requests.get(thumbnail_url)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '')
            content_length = int(response.headers.get('Content-Length', '0'))

            # Check if the content type is JPEG and the file size is less than 200 kB
            if content_type.startswith('image/jpeg') and content_length < 200 * 1024:
                return thumbnail_url  # Valid thumbnail URL
    except Exception as e:
        pass

    return None