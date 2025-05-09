import asyncio
import discord
import re
import os
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S+/\d+--\S+/\d+/\d+')


bot = discord.Bot()

async def process_clip(ctx: discord.ApplicationContext, route: str):
  await ctx.defer(ephemeral=True)
    
  link = link_regex.match(route)
  if link:
      path = urlparse(link.group()).path
      route = route_regex.match(path[1:])
      if not route:
        await ctx.respond(content='no route found in the input provided')
        return
  else:
    route = route_regex.match(route)
    if not route:
      await ctx.respond(content='no route found in the input provided')
      return
  route = route.group()
  print(f'clipping {route}...')
  with TemporaryDirectory() as temp_dir:
    path = Path(os.path.join(temp_dir, 'clip.mp4')).resolve()
    proc = await asyncio.create_subprocess_exec('openpilot/tools/op.sh', 'clip', route, '-o', path, '-f', '10', cwd='openpilot', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
      await ctx.respond(content='clip failed due to unknown reason')
      return
    await ctx.respond(content=f"here's your clip for {route}:", file=discord.File(path))

@bot.command(
  description="clip an openpilot route",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL with timing info', required=True)
async def clip(ctx: discord.ApplicationContext, route: str):
  if ctx.author.bot:
    return
  await process_clip(ctx, route)

class Client(discord.Client):
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
  bot.run(discord_token)
