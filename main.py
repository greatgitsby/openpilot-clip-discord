import asyncio
import discord
import re
import os
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S+/\d+--\S+/\d+/\d+')

async def process_clip(route: str, message: discord.Message):
  log = f'clipping {route}...'
  print(log)
  thread = message.channel
  msg = await thread.send(log)
  with TemporaryDirectory() as temp_dir:
    path = Path(os.path.join(temp_dir, 'clip.mp4')).resolve()
    proc = await asyncio.create_subprocess_exec('openpilot/tools/op.sh', 'clip', route, '-o', path, cwd='openpilot', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
      await thread.send(f'''
      ```
      {stderr.decode()}        
      ```
      ''')
      return
    await msg.edit(content=f"here's your clip for {route}:", attachments=(discord.File(path),))

class MyClient(discord.Client):
  async def on_ready(self):
    print('Logged on as', self.user)

  async def on_message(self, message: discord.Message):
    content = message.content
    if message.author == self.user:
      return
     
    link = link_regex.match(message.content)
    if link:
        path = urlparse(link.group()).path
        route = route_regex.match(path[1:])
        if route:
          asyncio.create_task(process_clip(route.group(), message))
    else:
      route = route_regex.match(content)
      if route:
        asyncio.create_task(process_clip(route.group(), message))

if __name__ == "__main__":
  discord_token = os.environ.get('DISCORD_TOKEN')
  if discord_token is None:
    raise EnvironmentError('Missing discord token')

  intents = discord.Intents.default()
  intents.messages = True
  intents.message_content = True

  client = MyClient(intents=intents)
  client.run(discord_token)