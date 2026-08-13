import sys
import asyncio
sys.path.insert(0, ".")
from parser.morelogin_client import get_default_client

async def main():
    async with get_default_client() as ml:
        profiles = await ml.get_profiles(group_name='GGSeller')
        print(f"Found {len(profiles)} profiles in GGSeller:")
        for p in profiles:
            env_id = p.get('envId', '')
            name = p.get('name', p.get('envName', ''))
            print(f"Profile: {name}, envId: {env_id}")

if __name__ == "__main__":
    asyncio.run(main())
