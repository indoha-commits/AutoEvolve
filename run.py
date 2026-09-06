import asyncio
import json
import sys

from agents.founder import run_founder


async def main():
    if len(sys.argv) < 2:
        print('Usage: python run.py "your request"')
        return

    message = " ".join(sys.argv[1:])

    result = await run_founder(message)

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
