import os, sys
from .cli import main

if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        import uvicorn
        uvicorn.run("firstpr.server:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
    else:
        main()
