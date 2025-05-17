import asyncio
import discord
import re
import os
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from openpilot.tools.lib.route import Route

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S{16}\/\S{8}--\S{10}')
route_with_time_regex = re.compile(r'\S{16}\/\S{8}--\S{10}\/\d+\/\d+')

@dataclass
class ClipRequest:
  ctx: discord.ApplicationContext
  route: str
  title: str | None
  is_bookmark: bool = False
  flag_time: int | None = None

queue = asyncio.Queue[ClipRequest]()
bot = discord.Bot()

MAX_CLIP_LEN_S = int(os.environ.get('MAX_CLIP_LEN', '30'))
WORKERS = int(os.environ.get('WORKERS', '1'))

def get_user_flags(route: str):
  route = Route(route)
  user_flags_at_time = []

  def process_segment(segment):
    for event in segment.events:
      if event['type'] == 'user_flag':
        time_ms = event['route_offset_millis']
        time_sec = round(time_ms / 1000)
        user_flags_at_time.append(time_sec)

  with ThreadPoolExecutor(max_workers=WORKERS * 8) as executor:
    executor.map(process_segment, route.segments)

  return sorted(user_flags_at_time)


def format_route(route: str):
  return f'[`{route}`](https://connect.comma.ai/{route})'


def format_time(seconds: int) -> str:
  minutes = seconds // 60
  remaining_seconds = seconds % 60
  return f'`{minutes:02d}:{remaining_seconds:02d}`'


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


async def process_clip(ctx: discord.ApplicationContext, route: str, title: str, is_bookmark: bool = False, flag_time: int | None = None):
  print(f'{ctx.interaction.user.display_name} ({ctx.interaction.user.id}) clipping {route}' )
  if not is_bookmark:
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
        error_msg = f'clip of {format_route(route)} failed due to unknown reason:\n\n```\n{stderr.decode()}\n```'
        if is_bookmark:
          await ctx.respond(content=error_msg, ephemeral=True)
        else:
          await ctx.edit(content=error_msg)
      else:
        content = f'clipped {format_route(route)}'
        if flag_time is not None:
          content += f', bookmarked at {format_time(flag_time)}'
        content += ':'
        if is_bookmark:
          await ctx.respond(content=content, file=discord.File(path), view=VideoPreview(ctx, route, discord.File(path)), ephemeral=True)
        else:
          await ctx.edit(content=content, file=discord.File(path), view=VideoPreview(ctx, route, discord.File(path)))
  except Exception as e:
    print('error processing clip', str(e))
    error_msg = f'clip of {format_route(route)} failed due to unknown reason:\n\n```\n{str(e)}\n```'
    if is_bookmark:
      await ctx.respond(content=error_msg, ephemeral=True)
    else:
      await ctx.edit(content=error_msg)


async def worker(name: str):
  print(f'started worker {name}')
  while True:
    request = await queue.get()
    try:
      await process_clip(request.ctx, request.route, request.title, request.is_bookmark, request.flag_time)
    finally:
      queue.task_done()


def get_route_and_time(route: str) -> tuple[str, int, int] | None:
  link = link_regex.match(route)
  if link:
    path = urlparse(link.group()).path
    route = route_with_time_regex.match(path[1:])
  else:
    route = route_with_time_regex.match(route)

  if not route:
    return None

  route = route.group()
  start_str, end_str = route.split('/')[2:]
  start, end = int(start_str), int(end_str)
  return route, start, end


def get_route(route: str) -> str | None:
  link = link_regex.match(route)
  if link:
    path = urlparse(link.group()).path
    route = route_regex.match(path[1:])
  else:
    route = route_regex.match(route)

  if not route:
    return None

  return route.group()


async def preprocess_clip(ctx: discord.ApplicationContext, route: str, title: str, is_bookmark: bool = False, flag_time: int | None = None):
  await ctx.defer(ephemeral=True)
  parsed = get_route_and_time(route)

  if parsed is None:
    await ctx.respond(content='please enter a valid route or connect URL')
    return

  route, start, end = parsed
  if end - start > MAX_CLIP_LEN_S:
    await ctx.edit(content=f'cannot make a clip longer than {MAX_CLIP_LEN_S}s')
    return

  await ctx.edit(content=f'queued request, {queue.qsize()} in line ahead')
  await queue.put(ClipRequest(ctx, route, title, is_bookmark, flag_time))


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
  await preprocess_clip(ctx, route, title, False, None)


@bot.command(
  description="clip your openpilot route bookmarks",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL', required=True)
async def bookmarks(ctx: discord.ApplicationContext, route: str):
  if ctx.author.bot:
    return

  before_flag_buffer = 10
  after_flag_buffer = 5

  await ctx.defer(ephemeral=True)
  route = get_route(route)

  if route is None:
    await ctx.respond(content='please enter a valid route or connect URL')
    return

  flags = get_user_flags(route)
  msg = f'{len(flags)} bookmark{"" if len(flags) == 1 else "s"} during route {format_route(route)}, processing them...\n'

  clip_details = []
  for i in range(0, len(flags)):
    flag = flags[i]
    route_w_time = f'{route}/{flag-before_flag_buffer}/{flag+after_flag_buffer}'
    await queue.put(ClipRequest(ctx, route_w_time, None, True, flag))
    clip_details.append(f'clip {i+1}/{len(flags)} with bookmark at {format_time(flag)}')

  await ctx.respond(f'{msg}\n' + '\n'.join(clip_details))


@bot.listen(once=True)
async def on_ready():
  for i in range(WORKERS):
    asyncio.create_task(worker(f'clip-worker-{i}'))


if __name__ == "__main__":
  discord_token = os.environ.get('DISCORD_TOKEN')
  if discord_token is None:
    raise EnvironmentError('Missing discord token')
  bot.run(discord_token)

