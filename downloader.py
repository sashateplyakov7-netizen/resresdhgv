import aiohttp

async def download_tiktok(url: str) -> str | None:
    api_url = f"https://www.tikwm.com/api/?url={url}"
    
    async with aiohttp.ClientSession() as session:
        # Стучимся к API
        async with session.get(api_url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            
            if data.get("code") != 0:
                return None
            
            # Достаем ссылку на само видео и его ID
            video_url = data["data"]["play"]
            video_id = data["data"]["id"]
            file_path = f"{video_id}.mp4"
            
            # Качаем mp4-файл
            async with session.get(video_url) as v_resp:
                if v_resp.status == 200:
                    with open(file_path, "wb") as f:
                        f.write(await v_resp.read())
                    return file_path
    return None
