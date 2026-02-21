import os
import re
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

# .env опционально: локально подхватит, на хостинге если файла нет — не упадёт
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
CATEGORY_ID = int(os.getenv("CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))  # 0 если не нужен

# Роль, которую выдаём при принятии
ACCEPT_ROLE_ID = 1365464185160335530

# Роль "кандидат в фаму" — даём при подаче заявки, снимаем при решении
CANDIDATE_ROLE_ID = int(os.getenv("CANDIDATE_ROLE_ID", "1474821926236065792"))

# Голосовой канал для обзвона
CALL_VOICE_CHANNEL_ID = 1471258606820397108

# Через сколько секунд удалять канал после принятия/отказа
DELETE_DELAY_SECONDS = 300

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

TICKET_PREFIX = "ticket"


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-zа-я0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text[:25] if text else "user")


async def send_log(guild: discord.Guild, msg: str):
    if LOG_CHANNEL_ID == 0:
        return
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        await ch.send(msg)


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.manage_channels:
        return True
    return any(r.id == STAFF_ROLE_ID for r in member.roles)


def extract_user_id_from_topic(topic: str | None) -> int | None:
    if not topic:
        return None
    m = re.search(r"user_id=(\d+)", topic)
    if not m:
        return None
    return int(m.group(1))


async def delete_channel_later(channel: discord.TextChannel, delay: int, reason: str):
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason=reason)
    except Exception:
        pass


async def add_candidate_role(guild: discord.Guild, user_id: int, reason: str):
    role = guild.get_role(CANDIDATE_ROLE_ID)
    if role is None:
        return
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        if role not in member.roles:
            await member.add_roles(role, reason=reason)
    except Exception:
        pass


async def remove_candidate_role(guild: discord.Guild, user_id: int, reason: str):
    role = guild.get_role(CANDIDATE_ROLE_ID)
    if role is None:
        return
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        if role in member.roles:
            await member.remove_roles(role, reason=reason)
    except Exception:
        pass


# ======= Кнопки рассмотрения тикета =======
class TicketReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Принять", style=discord.ButtonStyle.success, custom_id="ticket_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Ошибка контекста.", ephemeral=True)

        applicant_id = extract_user_id_from_topic(channel.topic)
        if applicant_id is None:
            return await interaction.followup.send("❌ Не нашёл user_id в topic канала.", ephemeral=True)

        role = guild.get_role(ACCEPT_ROLE_ID)
        if role is None:
            return await interaction.followup.send("❌ Роль для выдачи не найдена. Проверь ACCEPT_ROLE_ID.", ephemeral=True)

        # выдаём роль принятого
        try:
            member = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
            await member.add_roles(role, reason=f"Заявка принята модератором {interaction.user}")
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Не могу выдать роль. Проверь права бота **Manage Roles** и чтобы роль бота была ВЫШЕ нужной роли.",
                ephemeral=True
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ Ошибка выдачи роли: {e}", ephemeral=True)

        # снимаем роль кандидата
        await remove_candidate_role(guild, applicant_id, reason=f"Заявка принята ({interaction.user})")

        await channel.send(f"✅ Заявка **принята**. Роль выдана. Модератор: {interaction.user.mention}")
        await send_log(guild, f"✅ Принято: {channel.mention} | заявитель {applicant_id} | модер {interaction.user}")

        asyncio.create_task(delete_channel_later(channel, DELETE_DELAY_SECONDS, "Ticket accepted - auto delete"))
        await interaction.followup.send(f"Готово ✅ Канал удалится через {DELETE_DELAY_SECONDS} сек.", ephemeral=True)

    @discord.ui.button(label="❌ Отказать", style=discord.ButtonStyle.danger, custom_id="ticket_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Ошибка контекста.", ephemeral=True)

        applicant_id = extract_user_id_from_topic(channel.topic)
        if applicant_id is not None:
            await remove_candidate_role(guild, applicant_id, reason=f"Заявка отклонена ({interaction.user})")

        await channel.send(f"❌ Заявка **отклонена**. Модератор: {interaction.user.mention}")
        await send_log(guild, f"❌ Отказ: {channel.mention} | заявитель {applicant_id} | модер {interaction.user}")

        asyncio.create_task(delete_channel_later(channel, DELETE_DELAY_SECONDS, "Ticket rejected - auto delete"))
        await interaction.followup.send(f"Готово ✅ Канал удалится через {DELETE_DELAY_SECONDS} сек.", ephemeral=True)

    @discord.ui.button(label="📞 Вызвать на обзвон", style=discord.ButtonStyle.primary, custom_id="ticket_call")
    async def call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Ошибка контекста.", ephemeral=True)

        applicant_id = extract_user_id_from_topic(channel.topic)
        if applicant_id is None:
            return await interaction.followup.send("❌ Не нашёл user_id в topic канала.", ephemeral=True)

        voice = guild.get_channel(CALL_VOICE_CHANNEL_ID)

        try:
            member = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
        except Exception:
            member = None

        voice_link = f"<#{CALL_VOICE_CHANNEL_ID}>"
        if voice is None:
            voice_link = f"канал: <#{CALL_VOICE_CHANNEL_ID}> (проверь ID)"

        if member:
            await channel.send(
                f"📞 {member.mention}, тебя вызывают на обзвон!\n"
                f"Заходи в голосовой: {voice_link}\n"
                f"Модератор: {interaction.user.mention}"
            )
            await send_log(guild, f"📞 Обзвон: {channel.mention} | заявитель {member} | модер {interaction.user}")

        else:
            await channel.send(
                f"📞 <@{applicant_id}>, тебя вызывают на обзвон!\n"
                f"Заходи в голосовой: {voice_link}\n"
                f"Модератор: {interaction.user.mention}"
            )
            await interaction.followup.send("Пингнул (через ID) ✅", ephemeral=True)


# ======= 10 вопросов: Page 1 (5 шт) =======
class ApplyModalPage1(discord.ui.Modal, title="Заявка в семью (1/2)"):
    q1_nick = discord.ui.TextInput(label="1. Ваш ник", required=True, max_length=50)
    q2_age = discord.ui.TextInput(label="2. Ваш возраст", required=True, max_length=10)
    q3_tz = discord.ui.TextInput(label="3. Ваш часовой пояс", required=True, max_length=20)
    q4_online = discord.ui.TextInput(label="4. Средний онлайн", required=True, max_length=50)
    q5_gta = discord.ui.TextInput(label="5. Как давно играете в GTA", required=True, max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "nick": str(self.q1_nick.value),
            "age": str(self.q2_age.value),
            "tz": str(self.q3_tz.value),
            "online": str(self.q4_online.value),
            "gta": str(self.q5_gta.value),
        }

        await interaction.response.send_message(
            "✅ **Страница 1/2 заполнена.**\nНажми кнопку ниже, чтобы открыть **2/2**.",
            ephemeral=True,
            view=ContinueView(data),
        )


class ContinueView(discord.ui.View):
    def __init__(self, page1_data: dict):
        super().__init__(timeout=120)
        self.page1_data = page1_data

    @discord.ui.button(label="➡️ (2/2)", style=discord.ButtonStyle.primary)
    async def go_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplyModalPage2(self.page1_data))


# ======= 10 вопросов: Page 2 (5 шт) + создание тикета =======
class ApplyModalPage2(discord.ui.Modal, title="Заявка в семью (2/2)"):
    def __init__(self, page1_data: dict):
        super().__init__()
        self.page1 = page1_data

    q6_name = discord.ui.TextInput(
        label="6. Какой контент предпочитаете?",
        required=False,
        max_length=50,
        placeholder="примеры эти в саму строчку (лень)"
    )
    q7_micro = discord.ui.TextInput(
        label="7. Как стреляешь хуйло от 1 до 10?",
        required=True,
        max_length=20,
        placeholder="1 - 10"
    )
    q8_platform = discord.ui.TextInput(
        label="8. Готовы сменить фраку/имя?",
        required=True,
        max_length=30
    )
    q9_exp = discord.ui.TextInput(
        label="9. Расскажи о себе еблан",
        required=False,
        max_length=120
    )
    q10_why = discord.ui.TextInput(
        label="10. Почему хотите к нам крутым?",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=400
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Это работает только на сервере.", ephemeral=True)

        if CATEGORY_ID == 0 or STAFF_ROLE_ID == 0:
            return await interaction.response.send_message("❌ Проверь CATEGORY_ID и STAFF_ROLE_ID в env", ephemeral=True)

        category = guild.get_channel(CATEGORY_ID)
        staff_role = guild.get_role(STAFF_ROLE_ID)

        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ CATEGORY_ID неверный (категория не найдена).", ephemeral=True)
        if staff_role is None:
            return await interaction.response.send_message("❌ STAFF_ROLE_ID неверный (роль не найдена).", ephemeral=True)

        # защита от повторной заявки
        for ch in category.text_channels:
            if ch.topic and f"user_id={interaction.user.id}" in ch.topic:
                return await interaction.response.send_message(
                    f"У тебя уже есть открытая заявка: {ch.mention}", ephemeral=True
                )

        ticket_name = f"{TICKET_PREFIX}-{slugify(self.page1['nick'])}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        channel = await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=overwrites,
            topic=f"Заявка | user_id={interaction.user.id}",
            reason="Создание тикета",
        )

        embed = discord.Embed(
            title="📋 Заявка в семью",
            description=f"**Заявитель:** {interaction.user.mention}\nТикет: `{channel.name}`",
            color=discord.Color.dark_green()
        )
        embed.set_author(
            name=f"{interaction.user} (ID: {interaction.user.id})",
            icon_url=interaction.user.display_avatar.url
        )

        answers = (
            f"**1. Ваш ник:** `{self.page1['nick'] or '—'}`\n"
            f"**2. Ваш возраст:** `{self.page1['age'] or '—'}`\n"
            f"**3. Ваш часовой пояс:** `{self.page1['tz'] or '—'}`\n"
            f"**4. Средний онлайн:** `{self.page1['online'] or '—'}`\n"
            f"**5. Как давно играете в GTA:** `{self.page1['gta'] or '—'}`\n"
            f"**6. Какой контент предпочитаете?:** `{self.q6_name.value or '—'}`\n"
            f"**7. Как стреляешь хуйло от 1 до 10?:** `{self.q7_micro.value or '—'}`\n"
            f"**8. Готовы сменить фраку/имя?:** `{self.q8_platform.value or '—'}`\n"
            f"**9. Расскажи о себе еблан:** {(self.q9_exp.value or '—')}\n"
            f"**10. Почему хотите к нам крутым?:** {(self.q10_why.value or '—')}"
        )

        embed.add_field(name="Ответы", value=answers[:1024], inline=False)

        if len(answers) > 1024:
            embed.add_field(name="9. Расскажи о себе еблан", value=(self.q9_exp.value or "—")[:1024], inline=False)
            embed.add_field(name="10. Почему хотите к нам крутым?", value=(self.q10_why.value or "—")[:1024], inline=False)

        embed.set_footer(text="Кнопки ниже: принять / отказать / вызвать на обзвон.")

        await channel.send(
            content=f"{interaction.user.mention} {staff_role.mention}\nНовая заявка 👇",
            embed=embed,
            view=TicketReviewView()
        )

        # выдаём роль кандидата сразу после подачи заявки
        await add_candidate_role(guild, interaction.user.id, reason="Заявка подана (кандидат в фаму)")

        await send_log(guild, f"📩 Создан тикет {channel.mention} от {interaction.user}.")
        await interaction.response.send_message(f"✅ Заявка отправлена! Канал: {channel.mention}", ephemeral=True)


# ======= Панель с кнопкой "Заполнить" =======
class ApplyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 Заполнить заявку", style=discord.ButtonStyle.success, custom_id="apply_open_modal")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplyModalPage1())


@bot.event
async def on_ready():
    print("=== BOT VERSION: APPLY 10Q + ROLE + CALL + AUTODELETE ===")

    bot.add_view(ApplyPanelView())
    bot.add_view(TicketReviewView())

    try:
        if GUILD_ID != 0:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print("✅ Synced:", [c.name for c in synced])
        else:
            synced = await bot.tree.sync()
            print("✅ Synced globally:", [c.name for c in synced])
    except Exception as e:
        print("❌ Sync failed:", e)

    print(f"✅ Logged in as {bot.user}")


@bot.tree.command(name="setup_apply", description="Создать панель заявки (кнопка + форма) в этом канале")
@app_commands.checks.has_permissions(administrator=True)
async def setup_apply(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ОСТАВИТЬ ЗАЯВКУ НА ВСТУПЛЕНИЕ В СЕМЬЮ 📋",
        description="zxc",
        color=discord.Color.dark_green(),
    )
    await interaction.channel.send(embed=embed, view=ApplyPanelView())
    await interaction.response.send_message("✅ Панель создана.", ephemeral=True)


@bot.tree.command(name="resync", description="Пересинк (админ)")
@app_commands.checks.has_permissions(administrator=True)
async def resync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    if GUILD_ID == 0:
        return await interaction.followup.send("❌ В env нет GUILD_ID", ephemeral=True)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)

    await interaction.followup.send(f"✅ Resynced: {[c.name for c in synced]}", ephemeral=True)


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN не найден. Проверь переменные окружения на хостинге (ENV).")

if GUILD_ID == 0:
    print("⚠️ GUILD_ID=0. Лучше прописать GUILD_ID в env, чтобы команды появлялись сразу.")

bot.run(TOKEN)
