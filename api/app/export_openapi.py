"""Write api/openapi.json from the app (6.3). Its own module: app.main imports app.openapi,
so the CLI living in app.openapi would be an import cycle."""

from app.main import app
from app.openapi import export

if __name__ == "__main__":
    print(f"wrote {export(app)}")
