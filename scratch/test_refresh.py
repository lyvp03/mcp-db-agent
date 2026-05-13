import asyncio
from services.registry import get_source
from services.schema_service import refresh_schema_cache
from dotenv import load_dotenv
load_dotenv()

async def main():
    source = get_source('demo_db_5')
    print('Refreshing schema...')
    await refresh_schema_cache(source)
    print('Done!')

if __name__ == '__main__':
    asyncio.run(main())
