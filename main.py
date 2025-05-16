import asyncio
import discord
import re
import requests
import os
from pathlib import Path
from urllib.parse import urlparse
from tempfile import TemporaryDirectory

from openpilot.tools.lib.route import Route

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S+/\S+--\S+/\d+/\d+')

queue = asyncio.Queue()
bot = discord.Bot()

MAX_CLIP_LEN_S = int(os.environ.get('MAX_CLIP_LEN', '30'))
WORKERS = int(os.environ.get('WORKERS', '1'))


def get_user_flags(route: str):
  route = Route(route)
  meta = route.metadata
  url = meta['url']
  user_flags_at_time = []
  for segment in route.segments:
    num = segment.name.segment_num
    event_url = url + '/' + str(num) + '/events.json'
    resp = requests.get(event_url).json()
    for event in resp:
      if event['type'] == 'user_flag':
        time_ms = event['route_offset_millis']
        time_sec = round(time_ms / 1000)
        user_flags_at_time.append(time_sec)
  return user_flags_at_time


def format_route(route: str):
  return f'[`{route}`](https://connect.comma.ai/{route})'


class VideoPreview(discord.ui.View):
  def __init__(self, ctx: discord.ApplicationContext, route: str, vid: discord.File):
    super().__init__(timeout=None)
    self.ctx = ctx
    self.route = route
    self.vid = vid

  @discord.ui.button(label='Post', style=discord.ButtonStyle.primary, emoji='▶️')
  async def post_button(self, button: discord.ui.Button, interaction: discord.Interaction):
    user_id = interaction.user.id
    await interaction.response.send_message(content=f'<@{user_id}> shared a clip: {format_route(self.route)}', file=self.vid)
    button.label = 'Posted'
    button.emoji = '✅'
    button.style = discord.ButtonStyle.green
    button.disabled = True
    await self.ctx.edit(view=self)


async def process_clip(ctx: discord.ApplicationContext, route: str, title: str, new_msg: bool = False):
  print(f'{ctx.interaction.user.display_name} ({ctx.interaction.user.id}) clipping {route}' )
  if not new_msg:
    await ctx.edit(content=f'clipping {format_route(route)}')
  try:
    with TemporaryDirectory() as temp_dir:
      path = Path(os.path.join(temp_dir, f'{route.replace("/", "-")}.mp4')).resolve()
      args = ['openpilot/tools/clip/run.py', route, '-o', path, '-f', '9']
      if title:
        args.extend(['-t', title])
      proc = await asyncio.create_subprocess_exec('openpilot/.venv/bin/python3', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

      stdout, stderr = await proc.communicate()
      if proc.returncode != 0:
        await ctx.edit(content=f'clip of {format_route(route)} failed due to unknown reason:\n\n```\n{stderr.decode()}\n```')
      else:
        if new_msg:
          await ctx.respond(ephemeral=True, content=f'clipped {format_route(route)}:', file=discord.File(path), view=VideoPreview(ctx, route, discord.File(path)))
        else:
          await ctx.edit(content=f'clipped {format_route(route)}:', file=discord.File(path), view=VideoPreview(ctx, route, discord.File(path)))
  except Exception as e:
    print('error processing clip', str(e))
    await ctx.edit(content=f'clip of {format_route(route)} failed due to unknown reason:\n\n```\n{str(e)}\n```')


async def worker(name: str):
  print(f'started worker {name}')
  while True:
    ctx, route, title, new_msg = await queue.get()
    try:
      await process_clip(ctx, route, title, new_msg)
    finally:
      queue.task_done()


async def preprocess_clip(ctx: discord.ApplicationContext, route: str, title: str):
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
  start_str, end_str = route.split('/')[2:]
  start, end = int(start_str), int(end_str)
  if end - start > MAX_CLIP_LEN_S:
    await ctx.edit(content=f'cannot make a clip longer than {MAX_CLIP_LEN_S}s')
  else:
    await ctx.edit(content=f'queued request, {queue.qsize()} in line ahead')
    await queue.put((ctx, route, title, False,))


@bot.command(
  description="get all user bookmarks",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL', required=True)
async def bookmarks(ctx: discord.ApplicationContext, route: str):
  if ctx.author.bot:
    return
  await ctx.defer(ephemeral=True)
  flags = get_user_flags(route)
  msg = f'{len(flags)} flag{"" if len(flags) == 1 else "s"} during route `{route}`, queued all for processing'
  for i in range(0, len(flags)):
    flag = flags[i]
    route_w_time = f'{route}/{flag-10}/{flag+5}'
    await queue.put((ctx, route_w_time, f'flag {i+1}', True,))
  await ctx.respond(msg)


@bot.command(
  description="clip an openpilot route",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL with timing info', required=True)
@discord.option("title", type=str, description='an optional title to overlay', min_length=1, max_length=80, required=False)
async def clip(ctx: discord.ApplicationContext, route: str, title: str):
  if ctx.author.bot:
    return
  await preprocess_clip(ctx, route, title)


@bot.listen(once=True)
async def on_ready():
  for i in range(WORKERS):
    asyncio.create_task(worker(f'clip-worker-{i}'))


if __name__ == "__main__":
  discord_token = os.environ.get('DISCORD_TOKEN')
  if discord_token is None:
    raise EnvironmentError('Missing discord token')
  bot.run(discord_token)

