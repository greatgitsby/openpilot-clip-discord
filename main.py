import asyncio
import discord
import re
import os
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S+/\d+--\S+/\d+/\d+')


queue = asyncio.Queue()
bot = discord.Bot()

class VideoPreview(discord.ui.View):
  def __init__(self, ctx: discord.ApplicationContext, route: str, vid: discord.File):
    super().__init__(timeout=None)
    self.ctx = ctx
    self.route = route
    self.vid = vid

  @discord.ui.button(label='Post', style=discord.ButtonStyle.primary, emoji='▶️')
  async def post_button(self, button: discord.ui.Button, interaction: discord.Interaction):
    user_id = interaction.user.id
    await interaction.response.send_message(content=f'<@{user_id}> posted a clip `{self.route}`', file=self.vid)
    button.label = 'Posted'
    button.emoji = '✅'
    button.style = discord.ButtonStyle.green
    button.disabled = True
    await self.ctx.edit(view=self)


async def worker(name: str):
  print(f'started worker {name}')
  while True:
    ctx, route, title = await queue.get()
    print(f'{ctx.interaction.user.display_name} ({ctx.interaction.user.id}) clipping route `{route}`' )
    await ctx.edit(content=f'clipping route {route}')
    try:
      with TemporaryDirectory() as temp_dir:
        path = Path(os.path.join(temp_dir, f'{route.replace("/", "-")}.mp4')).resolve()
        args = ['.venv/bin/python3', 'tools/clip/run.py', route, '-o', path, '-f', '10']
        if title:
          args.extend(['-t', title])
        proc = await asyncio.create_subprocess_exec(*args, cwd='openpilot', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
          await ctx.edit(content=f'clip failed due to unknown reason:\n\n```\n{stderr.decode()}\n```')
        else:
          await ctx.edit(content='', file=discord.File(path), view=VideoPreview(ctx, route, discord.File(path)))
    except Exception as e:
      print('error processing clip', str(e))
      await ctx.edit(content=f'clip failed due to unknown reason:\n\n```\n{str(e)}\n```')
    finally:
      queue.task_done()


async def process_clip(ctx: discord.ApplicationContext, route: str, title: str):
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
  await queue.put((ctx, route, title,))
  await ctx.edit(content='queued request')

@bot.command(
  description="clip an openpilot route",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL with timing info', required=True)
@discord.option("title", type=str, description='an optional title to overlay', min=1, max=80, required=False)
async def clip(ctx: discord.ApplicationContext, route: str, title: str):
  if ctx.author.bot:
    return
  await process_clip(ctx, route, title)


@bot.listen(once=True)
async def on_ready():
  for i in range(1):
    await asyncio.create_task(worker(f'clipper-worker-{i}'))


if __name__ == "__main__":
  discord_token = os.environ.get('DISCORD_TOKEN')
  if discord_token is None:
    raise EnvironmentError('Missing discord token')
  bot.run(discord_token)
