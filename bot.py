# library imports
import asyncio
import discord
from discord.ext import commands
from discord.utils import get
import random
import datetime
import logging
import traceback as tb
# script imports
import items
import tutorial
from roles import role
import dataStorage
from dataStorage import getPlayerData, setPlayerData, deletePlayerData, initializeDataStorage
import objectives
import permissions
import setup

currentGames = {}
availableGames = {}
allPlayers = {}

# bot settings
localStorage = True  # If this setting is True, json will be used instead of mongoDB for storage.
testingBot = True  # Makes the bot not try to intergate with the main server. Leave it on unless you know what you're doing



initializeDataStorage(localStorage)

client = commands.Bot(
    command_prefix=lambda bot, message: [f"<@!{bot.user.id}> ", f"<@{bot.user.id}> ", f"<@{bot.user.id}>",
                                         f"<@!{bot.user.id}> ",
                                         dataStorage.getGuildData(message.guild, "prefix", default="!")],
    intents=discord.Intents().all(), case_insensitive=True)

# roles that will be in the game no matter what. Do not add more than 4
requiredRoles = ["murderer", "doctor"]
# possible roles. The numbers mean how many players there have to be for that role to appear.
roles = {"detective": 4, "banker": 4, "thief": 4, "jailer": 5, "broadcaster": 6, "fool": 6, "hunter": 6, "werewolf": 7,
         "cupid": 8}

# other settings
mainServerInvite = "https://discord.gg/7bbHsHgE3v"
shortMainServerInvite = "discord.gg/7bbHsHgE3v"
# this no permission embed is no longer used in most commands, only old commands that only work in the main guild anyways
noPermissionEmbed = discord.Embed(title="You don't have permission to do that!",
                                  description="You don't have permission to use that command here.", color=0xff0000)


# code

class game:
    def __init__(self, guild, debug):
        self.guild = guild
        self.debug = debug

        # channels & core game info
        self.channels = []
        self.channelsRemoveByMorning = []
        self.channelsRemoveByNight = []
        self.players = []
        self.allPlayers = []
        self.willDieNextMorning = []
        self.started = False
        self.day = 0
        self.nightTime = False
        self.countDown = False
        self.spectators = []
        self.playersThatVoted = []
        self.victory = None
        # gold
        self.goldPerDay = 1
        self.fivePlayersLeftGoldIncrease = False
        # skipping
        self.startNow = False
        self.skipVotingTime = False
        self.skipNight = False
        # voting
        self.extendVotingTime = False
        self.timesVotingTimeExtended = 0
        self.voteTime = False
        # fool
        self.foolKilled = False
        self.foolWin = False
        # default weather
        self.weatherIntensity = 1
        self.moon = 3

    async def createGame(self):
        # create role
        if self.guild != mainGuild:
            gameRolePosition = self.guild.self_role.position - 1
        else:
            gameRolePosition = mainGameRolePosition
        self.role = await self.guild.create_role()
        await self.role.edit(name="Waiting for game to start",
                             permissions=discord.Permissions(read_message_history=True, read_messages=True), hoist=True)
        # "temp" fix cuz I can't be bothered fixing this issue right now
        try:
            await self.role.edit(position=gameRolePosition)
        except:
            pass

        self.spectatorRole = await self.guild.create_role()
        if self.guild != mainGuild:
            gameRolePosition = self.guild.self_role.position - 1
        else:
            gameRolePosition = mainGameRolePosition - 1
        await self.spectatorRole.edit(name="Spectator",
                                      permissions=discord.Permissions(read_message_history=True, read_messages=True),
                                      hoist=True)
        try:
            await self.spectatorRole.edit(position=gameRolePosition)
        except:
            pass
        if dataStorage.getGuildData(self.guild, "useJoinChannel"):
            joiningChannel = self.guild.get_channel(dataStorage.getGuildData(self.guild, "joinChannel"))
            if joiningChannel is not None:
                await joiningChannel.set_permissions(self.role, read_messages=False)

        # create category
        self.category = await self.guild.create_category("game")
        await self.category.set_permissions(self.guild.me, send_messages=True, read_messages=True)
        await self.category.set_permissions(self.role, read_messages=True, send_messages=True)
        await self.category.set_permissions(self.guild.default_role, read_messages=False)
        await self.category.set_permissions(self.spectatorRole, read_messages=False, send_messages=False)
        await self.category.edit(position=0)

        # create main game channel
        self.mainChannel = await self.category.create_text_channel("Game")
        await self.mainChannel.set_permissions(self.spectatorRole, read_messages=True, send_messages=False)
        self.channels.append(self.mainChannel)

        self.voiceChannel = None
        if dataStorage.getGuildData(self.guild, "gameVoiceChannel", default=False):
            self.voiceChannel = await self.category.create_voice_channel("Game")
            await self.voiceChannel.set_permissions(self.guild.default_role, view_channel=False)
            await self.voiceChannel.set_permissions(self.role, view_channel=True)
            self.channels.append(self.voiceChannel)


        # add self to the running games list
        if not self.guild.id in currentGames:
            currentGames[self.guild.id] = []
        currentGames[self.guild.id].append(self)
        # add self to list of games that can be joined
        if not self.guild.id in availableGames:
            availableGames[self.guild.id] = []
        availableGames[self.guild.id].append(self)

        if self.guild == mainGuild:
            if not self.debug:
                await notificationChannel.send(f"{newGamesRole.mention}",
                                               embed=discord.Embed(title="A new game has just been created!",
                                                                   description=f"Someone just started a new game. You can join it by using !join in {joiningChannel.mention}",
                                                                   color=0x00b8ff))
        print(f"New game created in guild {self.guild.id} with {self.guild.member_count} members!")

    # add player
    async def addPlayer(self, member):
        # create new player instance
        newPlayer = player(member, self)
        # add the instance to the list of players in this game
        self.players.append(newPlayer)
        # add the player to a list of all players playing a game
        if not self.guild.id in allPlayers:
            allPlayers[self.guild.id] = []
        allPlayers[self.guild.id].append(newPlayer)
        # give the member the role
        await member.add_roles(self.role)

        # check if there are enough players to start the game
        if len(self.players) >= dataStorage.getGuildData(self.guild, "minPlayers", default=4):
            embed = discord.Embed(title=f":heavy_plus_sign: **{newPlayer.member.display_name} joined the game!**",
                                  description=f"The game will start soon!", color=0x0088ff)
            await self.mainChannel.send(f"{newPlayer.member.mention}", embed=embed)
            # start the countdown for the game to start
            await self.startCountdown()

        else:
            if dataStorage.getGuildData(self.guild, "minPlayers", default=4) - len(self.players) == 1:
                await self.mainChannel.send(f"{newPlayer.member.mention}", embed=discord.Embed(
                    title=f":heavy_plus_sign: **{newPlayer.member.display_name} joined the game!**",
                    description=f"{dataStorage.getGuildData(self.guild, 'minPlayers', default=4) - len(self.players)} more player is required for the game to start.",
                    color=0x0088ff))

            else:
                await self.mainChannel.send(f"{newPlayer.member.mention}", embed=discord.Embed(
                    title=f":heavy_plus_sign: **{newPlayer.member.display_name} joined the game!**",
                    description=f"{dataStorage.getGuildData(self.guild, 'minPlayers', default=4) - len(self.players)} more players are required for the game to start.",
                    color=0x0088ff))

    async def addSpectator(self, member):
        self.spectators.append(member)
        await member.add_roles(self.spectatorRole)
        await self.mainChannel.send(f"{member.mention}",
                                    embed=discord.Embed(title=f"{member.display_name} is now spectating",
                                                        description="They can now view this channel, but not talk in it.",
                                                        color=0x0088ff))

    async def removeSpectator(self, member):
        if member in self.spectators:
            self.spectators.remove(member)
            await member.remove_roles(self.spectatorRole)

    async def removePlayer(self, player, **kwargs):
        player.inGame = False

        allPlayers[self.guild.id].remove(player)
        self.players.remove(player)

        if hasattr(player, "nightChannel"):
            self.channels.remove(player.nightChannel)
            await player.nightChannel.delete()
        if hasattr(player, "roleChannel"):
            self.channels.remove(player.roleChannel)
            await player.roleChannel.delete()
        if hasattr(player, "broadcastChannel"):
            self.channels.remove(player.broadcastChannel)
            await player.broadcastChannel.delete()

        await player.member.remove_roles(self.role)

        checkWin = True
        if player.inLove:
            if player.lover.dyingNow:
                checkWin = False

            player.lover.inLove = False
            player.inLove = False
            await player.loveChannel.delete()
        if checkWin:
            await self.checkWin()

        if len(self.players) <= 0:
            await self.cleanUp()

    async def killPlayer(self, player, mainEmbed, DMEmbed, **kwargs):
        shouldDie = True
        bypassItems = False
        if "bypassItems" in kwargs:
            bypassItems = kwargs["bypassItems"]
        if not bypassItems:
            for item in player.inventory:
                if shouldDie:
                    if type(item).__name__ == "ring":
                        shouldDie = False
                        await self.mainChannel.send(embed=discord.Embed(
                            title=f"{player.member.display_name} almost died, but their {item.name} saved them!",
                            description="It now disappeared from their inventory.", color=0x00ff00))
                        await items.removeFromInventory(item, player)

                    elif type(item).__name__ == "shield":
                        if random.randint(0, 1) == 1:
                            shouldDie = False
                            await self.mainChannel.send(embed=discord.Embed(
                                title=f"{player.member.display_name} almost died, but their {item.name} saved them!",
                                description="It now disappeared from their inventory.", color=0x00ff00))
                            await items.removeFromInventory(item, player)

                    elif type(item).__name__ == "potato":
                        if random.randint(1, 10) == 1:
                            shouldDie = False
                            await self.mainChannel.send(embed=discord.Embed(
                                title=f"{player.member.display_name} almost died, but their {item.name} saved them!",
                                description="It now disappeared from their inventory.", color=0x00ff00))
                            await items.removeFromInventory(item, player)

        if shouldDie:

            if player.role == "fool":
                self.foolKilled = True

            await self.mainChannel.send(embed=mainEmbed)
            try:
                await player.member.send(embed=DMEmbed)
            except:
                pass
            if player.inLove:
                if not player.lover.dyingNow:
                    player.dyingNow = True
                    await self.killPlayer(player.lover, discord.Embed(
                        title=f":skull: {player.lover.member.display_name} died because their lover died",
                        description=f"{player.lover.member.display_name}{player.lover.role.deadString}",
                        color=0xff0000), discord.Embed(title=":skull: You died because your lover died",
                                                       color=0xff0000))

            player.dyingNow = False
            await self.removePlayer(player)
            return True
        else:
            return False

    async def startCountdown(self):
        if not self.countDown:
            countDownCanceled = False
            self.countDown = True
            countDown = dataStorage.getGuildData(self.guild, "preGameTimer", default=120)

            if self.guild == mainGuild:
                if not self.debug:
                    await notificationChannel.send(f"{gamesStartingRole.mention}",
                                                   embed=discord.Embed(title="A new game is about to start!",
                                                                       description=f"A new game is about to start in {countDown} seconds. Join it using !join in {joiningChannel.mention}",
                                                                       color=0x00b8ff))
            # start countdown loop
            while countDown > 0:
                nums = [120, 90, 60, 30, 10, 5]
                if countDown in nums:
                    embed = discord.Embed(title=f"The game will start in {countDown} seconds",
                                          description=f"The game will start in {countDown} seconds to allow for more players to join.",
                                          color=0x0088ff)
                    await self.mainChannel.send(embed=embed)

                # wait one second
                await asyncio.sleep(1)

                # subtract 1 from the countdown
                countDown = countDown - 1

                if len(self.players) < dataStorage.getGuildData(self.guild, 'minPlayers', default=4):
                    self.countDown = False
                    countDownCanceled = True
                    await self.mainChannel.send(embed=discord.Embed(title="Countdown canceled because someone left",
                                                                    description="The countdown got canceled because someone left and there are no longer enough players to start the game",
                                                                    color=0xff0000))
                    break

                if self.startNow:
                    self.countDown = False
                    break

            if not countDownCanceled:
                await self.mainChannel.send("Game is starting, please wait...")

                # remove self from games that can be joined
                availableGames[self.guild.id].remove(self)

                await self.initializeGame()

    async def initializeGame(self):
        # shuffle players list so you can't tell as the broadcaster who is what role by looking at in what order people joined and in what order the roles are listed
        random.shuffle(self.players)
        self.started = True
        self.day = 0
        await self.role.edit(permissions=discord.Permissions(read_message_history=True, read_messages=False))
        for channel in self.guild.channels:
            if self.voiceChannel is not None:
                if channel != self.mainChannel and channel.id != self.voiceChannel.id:
                    try:
                        await channel.set_permissions(self.role, read_messages=False)
                    except:
                        pass
            else:
                if channel != self.mainChannel:
                    try:
                        await channel.set_permissions(self.role, read_messages=False)
                    except:
                        pass

        await self.mainChannel.set_permissions(self.role, read_messages=True, send_messages=True)

        await self.mainChannel.edit(name="Day time")

        await self.role.edit(name="In game")

        # players we have yet to give roles to
        playersToGiveRolesTo = randomizeList(self.players.copy())

        availableRoles = requiredRoles.copy()
        while len(playersToGiveRolesTo) > 0:
            if len(availableRoles) > 0:
                playersToGiveRolesTo[0].setRole(availableRoles[0])
                playersToGiveRolesTo.pop(0)
                availableRoles.pop(0)
            else:
                break

        # roles that have yet to be given
        availableRoles = randomizeList(getKeys(roles).copy())
        # loop if there are players that we need to give a role to
        while len(playersToGiveRolesTo) > 0:
            if len(availableRoles) <= 0:
                playersToGiveRolesTo[0].setRole("none")
                playersToGiveRolesTo.pop(0)

            elif roles[availableRoles[0]] > len(self.players):
                availableRoles.pop(0)

            else:
                playersToGiveRolesTo[0].setRole(availableRoles[0])
                playersToGiveRolesTo.pop(0)
                availableRoles.pop(0)

        for player in self.players:
            # create player specific night channel
            player.nightChannel = await self.category.create_text_channel("Night time")
            await player.nightChannel.set_permissions(self.role, read_messages=False)
            self.channels.append(player.nightChannel)

            if player.role.name != "none" and player.role.name != "banker" and player.role.name != "fool":
                player.roleChannel = await self.category.create_text_channel(player.role.name)
                await player.roleChannel.set_permissions(self.role, read_messages=False)
                self.channels.append(player.roleChannel)

            self.allPlayers = self.players.copy()
        if self.voiceChannel is not None:
            await self.voiceChannel.set_permissions(self.role, view_channel=True)

        await self.firstDay()

    async def firstDay(self):
        embed = discord.Embed(title="Welcome to murder mystery!",
                              description="There is a murderer here! In order to win the game, you must find who the murderer is and kill them. If you are the murderer, you need to kill everyone in order to win.",
                              color=0x0088ff)
        embed.add_field(name="Day time",
                        value="At day time everyone can vote to execute someone. You can vote using !vote <person>",
                        inline=False)
        embed.add_field(name="Night time",
                        value="At night time you can use the shop to buy new items. Use !shop and !buy at night time. Bought items can be used during day time with !use <item>",
                        inline=False)
        embed.add_field(name="Special roles",
                        value="Some people have special roles and will be able to use that role's ability at night time. There will be a separate channel at night time for your role.",
                        inline=False)
        embed.add_field(name="Good luck!", value="Your role will be revealed to you at night. Good luck and have fun!")
        await self.mainChannel.send(f"{self.role.mention}", embed=embed)
        if not self.debug:
            await asyncio.sleep(15)
        await self.mainChannel.send(embed=discord.Embed(title=":sunny: Day 0",
                                                        description="Night will approach soon! Your role will be revealed at night time",
                                                        color=0xfff100))
        if not self.debug:
            await asyncio.sleep(10)

        await self.makeNightTime()

    async def makeNightTime(self):

        self.nightTime = True
        await self.mainChannel.set_permissions(self.role, send_messages=False)

        while len(self.channelsRemoveByNight) > 0:
            channel = await client.fetch_channel(self.channelsRemoveByNight[0])
            try:
                await channel.delete()
            except:
                pass
            if channel in self.channels:
                self.channels.remove(channel)
            pass
            self.channelsRemoveByNight.remove(self.channelsRemoveByNight[0])

        if self.voiceChannel is not None:
            if dataStorage.getGuildData(self.guild, "lockVoiceChannelDuringNight", default=False):
                await self.voiceChannel.set_permissions(self.role, view_channel=False)
                for member in self.voiceChannel.members:
                    try:
                        await member.move_to(None)
                    except:
                        pass

        jailer = None
        emoji = ""

        for player in self.players:
            player.satelliteUsed = False
            player.role.abilityUsed = False
            player.whisperingTo = []
            await player.nightChannel.set_permissions(player.member, read_messages=True, send_messages=True)
            if self.moon == 1:
                emoji = ":new_moon:"
            elif self.moon == 2:
                emoji = ":waning_crescent_moon:"
            elif self.moon == 3:
                emoji = ":last_quarter_moon:"
            elif self.moon == 4:
                emoji = ":waning_gibbous_moon:"
            elif self.moon == 5:
                emoji = ":full_moon:"
            embed = discord.Embed(title=f"{emoji} Night time",
                                  description="It's now night time. You can't talk to other people in night time, but you can use !shop to use the shop during night time.")
            if self.day == 0:
                embed.add_field(name="First night:",
                                value="Your role will be revealed below. Some roles have special channels that can be used during night time, if that's the case the specific role's channel will be accessible during night time.")
            await player.nightChannel.send(embed=embed)

            if self.day == 0:
                await player.nightChannel.send(embed=player.role.revealEmbed)

        if self.day == 0:
            if self.findRole("werewolf") is not None:
                werewolf = self.findRole("werewolf")
                murderer = self.findRole("murderer")
                await murderer.nightChannel.send(embed=discord.Embed(
                    title=f":wolf: {werewolf.member.display_name} is the werewolf, and you're a team with them.",
                    description="The werewolf has to kill someone when it becomes full moon. You're teaming up with them to kill everyone else.\n**Do no try to kill them, they're your teammate.**",
                    color=0xffda83))
                await werewolf.nightChannel.send(embed=discord.Embed(
                    title=f":dagger: {murderer.member.display_name} is the murderer, and you're a team with them.",
                    description="You will team up with the murderer to kill everyone else. If the murderer dies, you automatically lose too.\n**Do not try to kill them, they're your teammate.**",
                    color=0xa80700))

        for player in self.players:

            if random.randint(1, 10) == 1:
                player.gold += 2
                await player.nightChannel.send(embed=discord.Embed(title="You found :coin: 2 gold",
                                                                   description="You found :coin: 2 gold lying on the floor. Lucky you!",
                                                                   color=0x00ff00))

            if self.weatherIntensity > 60 and self.weatherIntensity <= 80:
                num = random.randint(1, 10)
                goldNum = 0
                if num >= 5 and num <= 7:
                    goldNum = 1
                elif num == 8:
                    goldNum = 2
                elif num == 9:
                    goldNum = 3
                elif num == 10:
                    goldNum = 4

                if goldNum != 0:
                    player.gold += goldNum
                    await player.nightChannel.send(
                        embed=discord.Embed(title=f":dash: The wind blew :coin: {goldNum} gold to you!",
                                            description="Lucky you!", color=0x00ff00))

            if hasattr(player, "roleChannel"):
                if not hasattr(player.role, "unlockRoleChannelAtNightTime"):
                    await player.roleChannel.set_permissions(player.member, read_messages=True, send_messages=True)
                else:
                    await player.roleChannel.set_permissions(player.member, read_messages=True, send_messages=False)

                await player.role.sendRoleChannelEmbed()

            if player.role.name == "broadcaster":
                for bc in player.role.broadcastsToBeSent:
                    await bc.send()

            elif player.role.name == "jailer":
                jailer = player

            elif player.role.name == "werewolf":
                player.role.killedSomeone = False

        if jailer is not None:
            if hasattr(jailer.role, "jailedNext"):
                if jailer.role.jailedNext is not None:
                    if jailer.role.jailedNext in self.players:
                        jailer.role.jailedNext.inJail = True
                        jailer.role.jailChannel = await self.category.create_text_channel("Jail")
                        self.channels.append(jailer.role.jailChannel)
                        self.channelsRemoveByMorning.append(jailer.role.jailChannel)

                        await jailer.role.jailChannel.set_permissions(self.role, read_messages=False,
                                                                      send_messages=False)
                        await jailer.role.jailChannel.set_permissions(jailer.role.jailedNext.member, read_messages=True,
                                                                      send_messages=False)
                        await jailer.role.jailedNext.nightChannel.set_permissions(jailer.role.jailedNext.member,
                                                                                  read_messages=True,
                                                                                  send_messages=False)
                        if hasattr(jailer.role.jailedNext, "roleChannel"):
                            await jailer.role.jailedNext.roleChannel.set_permissions(jailer.role.jailedNext.member,
                                                                                     read_messages=True,
                                                                                     send_messages=False)

                        await jailer.role.jailChannel.send(
                            f"{jailer.role.jailedNext.member.mention}",
                            embed=discord.Embed(
                                title=f"You are now in jail",
                                description=f"While in jail, you can't use the shop or your role's ability.",
                                color=0x4a4a4a))

                        await jailer.role.jailChannel.edit(position=0)

                        jailer.role.jailedNext = None

        if self.weatherIntensity > 95:
            await self.sendToAllNightChannels(
                embed=discord.Embed(title=":warning: :cloud_tornado: Tornado warning! :warning:",
                                    description="There will soon be a tornado. If you don't a :house: house, there's a 30% chance that you will die next morning.\nYou can buy a :house: house from !shop",
                                    color=0xff0000))

        count = dataStorage.getGuildData(self.guild, "nightTimeTimer", default=60)
        while count >= 0:
            if self.skipNight:
                self.skipNight = False
                break
            if count == 30:
                for player in self.players:
                    await player.nightChannel.send(
                        embed=discord.Embed(title=":first_quarter_moon: The sun is about to rise!",
                                            description="The sun will rise in 30 seconds."))
            elif count == 15:
                for player in self.players:
                    await player.nightChannel.send(
                        embed=discord.Embed(title=":waxing_crescent_moon: The sun is about to rise!",
                                            description="The sun will rise in 15 seconds."))
            elif count == 10:
                for player in self.players:
                    await player.nightChannel.send(
                        embed=discord.Embed(title=":sunrise_over_mountains: The sun is about to rise!",
                                            description="The sun will rise in 5 seconds."))

            await asyncio.sleep(1)
            count -= 1

        await self.dayTime()

    async def dayTime(self):
        self.nightTime = False
        self.day = self.day + 1

        await self.mainChannel.set_permissions(self.role, send_messages=True, read_messages=True)
        if self.voiceChannel is not None:
            if dataStorage.getGuildData(self.guild, "lockVoiceChannelDuringNight", default=False):
                await self.voiceChannel.set_permissions(self.role, view_channel=True)

        await self.mainChannel.send(embed=discord.Embed(title=f":sunny: Day {self.day}",
                                                        description="The sun is rising, good morning everyone!",
                                                        color=0xfff100))
        for player in self.players:
            await player.nightChannel.set_permissions(player.member, read_messages=False, send_messages=False)
            if hasattr(player, "roleChannel"):
                await player.roleChannel.set_permissions(player.member, read_messages=False, send_messages=False)

        for channel in self.channelsRemoveByMorning:
            self.channelsRemoveByMorning.remove(channel)
            if channel in self.channels:
                self.channels.remove(channel)
            try:
                await channel.delete()
            except:
                pass

        # kill random person if the werewolf didn't kill someone during full moon
        if self.findRole("werewolf") is not None:
            if self.moon == 5:
                if not self.findRole("werewolf").role.killedSomeone:
                    playersList = self.getPlayersListExcluding([self.findRole("werewolf"), self.findRole("murderer")])
                    random.shuffle(playersList)
                    self.willDieNextMorning.append(
                        {"player": playersList[0], "title": " got killed",
                         "DM": ":skull: You got killed by the werewolf!"})

        # kill everyone that should die this morning
        for deathData in self.willDieNextMorning:
            plr = deathData["player"]
            if plr in self.players:
                embedTitle = deathData["title"]
                if await self.killPlayer(deathData["player"],
                                         discord.Embed(title=f":skull: {plr.member.display_name} {embedTitle}",
                                                       description=f"{plr.member.display_name}{plr.role.deadString}",
                                                       color=0xff000d), discord.Embed(title=deathData["DM"],
                                                                                      description=f"You almost made it to day {self.day}",
                                                                                      color=0xff000d)):
                    if "deathCause" in deathData:
                        if deathData["deathCause"] == "itemUsedOnPlayer":
                            if "killer" in deathData:
                                objectives.addObjectiveProgress(deathData["killer"].member, "killMurdererWithItem", 1)

                self.willDieNextMorning.remove(deathData)
            else:
                continue

        if self.weatherIntensity > 95:
            for player in self.players:
                hasHouse = False
                house = None
                for item in player.inventory:
                    if type(item).__name__ == "house":
                        hasHouse = True
                        house = item
                if random.randint(0, 10) <= 3:
                    if hasHouse:
                        await items.removeFromInventory(house, player)
                        await self.mainChannel.send(embed=discord.Embed(
                            title=f":house_abandoned: :cloud_tornado: {player.member.display_name}'s house got destroyed by the tornado",
                            description="But they survived themselves!", color=0xff0000))
                    else:
                        await self.killPlayer(player, discord.Embed(
                            title=f":skull: :cloud_tornado: {player.member.display_name} died in the tornado",
                            description=f"{player.member.display_name}{player.role.deadString}", color=0xff0000),
                                              discord.Embed(title=":skull: :cloud_tornado: You died in a tornado",
                                                            description="Next time buy a :house: house",
                                                            color=0xff0000))

        if len(self.players) <= 5:
            if not self.fivePlayersLeftGoldIncrease:
                self.goldPerDay += 1
                self.fivePlayersLeftGoldIncrease = True
                await self.mainChannel.send(
                    embed=discord.Embed(title=":chart_with_upwards_trend: Gold per day increased",
                                        description=f"Because 5 or less players are remaining, the gold received per day increased from {self.goldPerDay - 1} to {self.goldPerDay}",
                                        color=0x00ff00))

        if self.day % 3 == 0:
            self.goldPerDay += 1
            await self.mainChannel.send(embed=discord.Embed(title=":chart_with_upwards_trend: Gold per day increased",
                                                            description=f"Every 3 days the gold earned by day increases by 1. The gold received per day is now {self.goldPerDay}.",
                                                            color=0x00ff00))

        for player in self.players:
            if player.role.name != "banker":
                player.gold += self.goldPerDay
            else:
                player.gold += self.goldPerDay + 1

        await self.mainChannel.send(embed=discord.Embed(title=f":coin: Everyone received {self.goldPerDay} gold",
                                                        description=f"Every morning, everyone will receive :coin: {self.goldPerDay} gold.\nIf there is a :person_in_tuxedo: banker, then they received :coin: {self.goldPerDay + 1} gold.",
                                                        color=0x00b8ff))

        # lottery
        for player in self.players:
            if player.inJail:
                player.inJail = False

            for item in player.inventory:
                if type(item).__name__ == "ticket":
                    goldBeforePrize = player.gold
                    wonPrize = False
                    if random.randint(0, 100) == 1:
                        player.gold += 30
                        wonPrize = True
                    elif random.randint(0, 50) == 1 and not wonPrize:
                        player.gold += 20
                        wonPrize = True
                    elif random.randint(0, 20) == 1 and not wonPrize:
                        player.gold += 10
                        wonPrize = True
                    elif random.randint(0, 7) == 1 and not wonPrize:
                        player.gold += 5
                        wonPrize = True

                    if wonPrize:
                        await self.mainChannel.send(embed=discord.Embed(
                            title=f":moneybag: {player.member.display_name} won :coin: {player.gold - goldBeforePrize} gold in the lottery!",
                            description="Their lottery ticket is now no longer in their inventory", color=0x00ff00))
                    else:
                        await self.mainChannel.send(embed=discord.Embed(
                            title=f":money_with_wings: {player.member.display_name} lost the lottery",
                            description="Their lottery ticket is now no longer in their inventory", color=0xff0000))
                    await items.removeFromInventory(item, player)
                    break
        if not self.debug:
            await asyncio.sleep(10)

        # voting time
        for player in self.players:
            player.voted = False
            player.votes = 0
        self.voteTime = True
        desc = "Vote on who you think is the murderer with !vote <@username>.\n\nYou will have 120 seconds to vote."
        if len(self.allPlayers) >= roles["fool"] and not self.foolKilled:
            desc += "\n\nIt's possible that there is a :clown: fool here, if they get voted to be executed they win."
        await self.mainChannel.send(embed=discord.Embed(title="Vote to execute someone using !vote <player>",
                                                        description=desc,
                                                        color=0x00b8ff))

        extendedTooMuchMessageSent = False
        count = dataStorage.getGuildData(self.guild, "votingTime", default=120)
        while count >= 0:

            if len(self.playersThatVoted) == len(self.players):
                if count > 16:
                    await self.mainChannel.send(
                        embed=discord.Embed(title="Everyone has voted, voting time has been set to 15 seconds",
                                            description="Because everyone has voted, the voting time has been set to 15 seconds. If you want to change your vote, do it now.",
                                            color=0x0088ff))
                    count = 15

            if self.skipVotingTime:
                self.skipVotingTime = False
                break

            reminders = [90, 60, 30, 15, 10, 5]
            if count in reminders:
                await self.mainChannel.send(f"Voting ends in {count} seconds!")
            count -= 1

            if self.extendVotingTime:
                if count <= 15:
                    if self.timesVotingTimeExtended < 7:
                        count = 15
                        await self.mainChannel.send(
                            embed=discord.Embed(title=f"Voting time has been extended to {count} seconds",
                                                description=f"To prevent people from submitting their votes last-second and changing the voting result without other players knowing, the voting time has been extended to {count} seconds.",
                                                color=0x0088ff))
                    else:
                        if not extendedTooMuchMessageSent:
                            count = 15
                            await self.mainChannel.send(
                                embed=discord.Embed(title=f"Voting time has been extended to {count} seconds",
                                                    description=f"To prevent people from submitting their votes last-second and changing the voting result without other players knowing, the voting time has been extended to {count} seconds.",
                                                    color=0x0088ff))
                            await self.mainChannel.send(
                                embed=discord.Embed(title="Voting time will not get extended anymore",
                                                    description="The voting time got extended too many times and will no longer get extended now",
                                                    color=0xff0000))
                            extendedTooMuchMessageSent = True
                self.extendVotingTime = False

            await asyncio.sleep(1)

        self.voteTime = False
        # sort highest voted player first
        votes = self.players.copy()
        votes.sort(reverse=True, key=lambda x: x.votes)
        # create embed
        tie = False
        if votes[0].votes != votes[1].votes:
            voteResultEmbedTitle = f"Vote results are in, {votes[0].member.display_name} will be executed"
        else:
            voteResultEmbedTitle = "There was a tie, no one will be executed."
            tie = True
        embedDesc = "These are the results of the vote. The player with the most votes will get executed.\n"
        for player in votes:
            embedDesc = embedDesc + f"\n{player.member.mention}: {player.votes}"
        await self.mainChannel.send(
            embed=discord.Embed(title=voteResultEmbedTitle, description=embedDesc, color=0x00b8ff))
        self.playersThatVoted = []

        for player in self.players:
            if player.voted:
                if player.votedOn.role == "murderer":
                    objectives.addObjectiveProgress(player.member, "voteOnMurderer", 1)
                    if self.day == 1:
                        if len(self.allPlayers) >= 6:
                            objectives.addObjectiveProgress(player.member, "dayOneMurdererVote6PlayersOrMore", 1)

        await asyncio.sleep(2)
        # kill player
        if not tie:
            if votes[0].role.name != "fool":
                await self.killPlayer(votes[0], discord.Embed(title=f"{votes[0].member.display_name} got executed",
                                                              description=f"{votes[0].member.display_name}{votes[0].role.deadString}",
                                                              color=0xff000d),
                                      discord.Embed(title=f":skull: You died because you got executed.",
                                                    description="Try convincing the other players that you're not the murderer next time!",
                                                    color=0xff000d), bypassItems=True)

            else:
                await self.mainChannel.send(embed=discord.Embed(title=f"{votes[0].member.display_name} got executed",
                                                                description=f"{votes[0].member.display_name}{votes[0].role.deadString}",
                                                                color=0xff000d))
                self.foolWin = True
                await asyncio.sleep(1)
                await self.mainChannel.send(embed=discord.Embed(title=":clown: Fool wins!",
                                                                description="The fool has won because he got executed!",
                                                                color=0xfff100))
                await asyncio.sleep(10)
                await self.stopGame()

        await asyncio.sleep(10)
        if self.victory is None:
            await self.mainChannel.send(embed=discord.Embed(title=":white_sun_small_cloud: The sun is about to set!",
                                                            description="Night will approach in 20 seconds.",
                                                            color=0xffe800))
            await asyncio.sleep(2)
            # weather
            self.weatherIntensity = random.randint(1, 100)
            self.moon = random.randint(1, 5)
            desc = "Welcome to today's weather forecast. Here is the predicted weather for tonight:\n\n"
            if self.weatherIntensity <= 60:
                desc += ":milky_way: Tonight there will be a clear sky without any clouds or extreme weather."
            elif 60 < self.weatherIntensity <= 80:
                desc += ":dash: Tonight it will be a bit windy. If you're lucky some :coin: gold might even blow to your home!"
            elif 80 < self.weatherIntensity <= 90:
                desc += ":thunder_cloud_rain: Tonight there will be a thunderstorm and broadcasting communication systems might not work."
            elif 90 < self.weatherIntensity <= 95:
                desc += ":fog: Tonight it will be very foggy and the :spy: detective might not be able to do their work."
            elif self.weatherIntensity > 95:
                desc += ":cloud_tornado: Tonight there will be a tornado. It is highly recommended to seek shelter for tonight! The shop will sell a :house: house for you to hide in during the tornado."
            desc += "\n"
            if self.moon == 1:
                desc += ":new_moon: Also tonight the moon won't be visible. It will be very dark and the :dagger: murderer might not have enough light locate someone to kill."
            elif self.moon == 2:
                desc += ":waning_crescent_moon: Also tonight the moon will be partially visible."
            elif self.moon == 3:
                desc += ":last_quarter_moon: Also tonight the moon will be partially visible."
            elif self.moon == 4:
                desc += ":waning_gibbous_moon: Also tonight the moon will be partially visible."
            elif self.moon == 5:
                desc += ":full_moon: Also tonight there will be a full moon. If there is a :wolf: werewolf then they might kill someone."
            desc += "\n\nThat was the weather report for upcoming night. Goodbye!"

            await self.mainChannel.send(
                embed=discord.Embed(title=":radio: Weather forecast", description=desc, color=0x00ff00))

            await asyncio.sleep(8)
            await self.mainChannel.send(embed=discord.Embed(title=":white_sun_cloud: The sun is about to set!",
                                                            description="Night will approach in 10 seconds.",
                                                            color=0xffe800))
            await asyncio.sleep(5)
            await self.mainChannel.send(embed=discord.Embed(title=":city_sunset: The sun is about to set!",
                                                            description="Night will approach in 5 seconds.",
                                                            color=0xffe800))
            await asyncio.sleep(5)
            await self.makeNightTime()

    async def sendToAllNightChannels(self, **kwargs):
        if self.nightTime:
            if "msg" in kwargs:
                if "embed" in kwargs:
                    for plr in self.players:
                        await plr.nightChannel.send(kwargs["msg"], embed=kwargs["embed"])
                else:
                    for plr in self.players:
                        await plr.nightChannel.send(kwargs["msg"])

            elif "embed" in kwargs:
                for plr in self.players:
                    await plr.nightChannel.send(embed=kwargs["embed"])

    # returns a list of players excluding a certain player
    def getPlayersListExcluding(self, arg):
        if type(arg).__name__ == "player":
            copy = self.players.copy()
            copy.remove(arg)
            return copy
        elif type(arg).__name__ == "list":
            copy = self.players.copy()
            argCopy = arg.copy()
            for plr in argCopy:
                copy.remove(plr)
            return copy
        else:
            return None

    # returns the player with a certain role
    def findRole(self, roleName):
        for player in self.players:
            if player.role.name == roleName:
                return player
        return None

    async def checkWin(self):

        if self.started:
            foundMurderer = False
            for player in self.players:
                if player.role.name == "murderer":
                    foundMurderer = True

            if foundMurderer:
                if len(self.players) <= 2:
                    for player in self.players:
                        await player.nightChannel.set_permissions(player.member, read_messages=False,
                                                                  send_messages=False)
                        if hasattr(player, "roleChannel"):
                            await player.roleChannel.set_permissions(player.member, read_messages=False,
                                                                     send_messages=False)

                    if self.findRole("murderer").inLove:
                        await self.mainChannel.send(
                            embed=discord.Embed(title=":dagger: :couple_with_heart: Murderer and their lover win!",
                                                description="The murderer killed everyone except their lover!\n\n\nGame will end in 10 seconds...",
                                                color=0xa80700))
                    else:
                        await self.mainChannel.send(embed=discord.Embed(title=":dagger: Murderer wins!",
                                                                        description="Only 1 player besides the murderer is still alive. The murderer kills the remaining player and wins the game!\n\n\nGame will end in 10 seconds...",
                                                                        color=0xa80700))
                    await self.mainChannel.set_permissions(self.role, read_messages=True, send_messages=True)
                    self.victory = False
                    await asyncio.sleep(10)
                    await self.stopGame()

                elif len(self.players) == 3:
                    if self.findRole("werewolf") is not None:
                        for player in self.players:
                            await player.nightChannel.set_permissions(player.member, read_messages=False,
                                                                      send_messages=False)
                            if hasattr(player, "roleChannel"):
                                await player.roleChannel.set_permissions(player.member, read_messages=False,
                                                                         send_messages=False)
                        if self.findRole("murderer").inLove:
                            await self.mainChannel.send(embed=discord.Embed(
                                title=":dagger: :couple_with_heart: :wolf: Murderer, their lover and the werewolf win!",
                                description="Only 1 player besides the murderer and werewolf is still alive. The murderer and werewolf kill the remaining player and wins the game!\n\n\nGame will end in 10 seconds...",
                                color=0xa80700))

                        else:
                            await self.mainChannel.send(embed=discord.Embed(
                                title=":dagger: :wolf: Murderer and werewolf win!",
                                description="Only 1 player besides the murderer and werewolf is still alive. The murderer and werewolf kill the remaining player and wins the game!\n\n\nGame will end in 10 seconds...",
                                color=0xa80700))
                        await self.mainChannel.set_permissions(self.role, read_messages=True, send_messages=True)
                        self.victory = False
                        await asyncio.sleep(10)
                        await self.stopGame()

            else:
                for player in self.players:
                    await player.nightChannel.set_permissions(player.member, read_messages=False, send_messages=False)
                    if hasattr(player, "roleChannel"):
                        await player.roleChannel.set_permissions(player.member, read_messages=False,
                                                                 send_messages=False)
                await self.mainChannel.send(embed=discord.Embed(title=":tada: Victory!",
                                                                description="The murderer has been killed! The villagers won!\n\n\nGame will end in 10 seconds...",
                                                                color=0x00ff00))
                await self.mainChannel.set_permissions(self.role, read_messages=True, send_messages=True)
                self.victory = True
                await asyncio.sleep(10)
                await self.stopGame()

    async def stopGame(self):
        await self.role.delete()
        # summary embed
        if not self.foolWin:
            if self.victory:
                embed = discord.Embed(title=":tada: Villagers won!",
                                      description="A game just ended because the murderer got killed!", color=0x00ff00)

                for plr in self.allPlayers:
                    if plr.role.name != "murderer" or plr.role.name != "werewolf" or plr.role.name != "fool":
                        setPlayerData(plr.member, "villagerWins", increase=1)  # increase wins
                    for plr in self.players:
                        if plr.role.name != "murderer" or plr.role.name != "werewolf" or plr.role.name != "fool":
                            objectives.addObjectiveProgress(plr.member, "villagerWinsNoDeath", 1)

            else:
                if self.findRole("werewolf") is not None:
                    if self.findRole("murderer").inLove:
                        embed = discord.Embed(
                            title=":dagger: :couple_with_heart: :wolf: Murderer, their lover and the werewolf won!",
                            description="A game just ended because the murderer and werewolf killed everybody!",
                            color=0xff0000)
                        setPlayerData(self.findRole("murderer").lover.member, "villagerWins", increase=1)
                    else:
                        embed = discord.Embed(title=":dagger: :wolf: Murderer and werewolf won!",
                                              description="A game just ended because the murderer and werewolf killed everybody!",
                                              color=0xff0000)
                    setPlayerData(self.findRole("werewolf").member, "werewolfWins", increase=1)  # increase wins
                else:
                    if self.findRole("murderer").inLove:
                        embed = discord.Embed(title=":dagger: :couple_with_heart: Murderer and their lover won!",
                                              description="A game just ended because the murderer killed everybody!",
                                              color=0xff0000)
                        setPlayerData(self.findRole("murderer").lover.member, "villagerWins", increase=1)
                    else:
                        embed = discord.Embed(title=":dagger: Murderer won!",
                                              description="A game just ended because the murderer killed everybody!",
                                              color=0xff0000)
                setPlayerData(self.findRole("murderer").member, "murdererWins", increase=1)  # increase wins
                objectives.addObjectiveProgress(self.findRole("murderer").member, "murdererWins", 1)
        else:
            embed = discord.Embed(title=":clown: Fool won!",
                                  description="A game just ended because the fool got executed",
                                  color=0xfff100)
            setPlayerData(self.findRole("fool").member, "foolWins", increase=1)  # increase wins

        embed.add_field(name="Game summary",
                        value=f":sunny: Day {self.day}\n:busts_in_silhouette: Total players: {len(self.allPlayers)}\n:bust_in_silhouette: Players remaining: {len(self.players)}\n\n",
                        inline=False)
        rolelessPlayers = []
        for player in self.allPlayers:
            if player.role.name != "none":
                embed.add_field(name=f"{player.role.fancyName}", value=f"{player.member.mention}", inline=True)
            else:
                rolelessPlayers.append(player)
            setPlayerData(player.member, "gamesPlayed", increase=1)
        rolelessPlayersFieldValue = ""
        for player in rolelessPlayers:
            rolelessPlayersFieldValue += f"{player.member.mention} "
        if rolelessPlayersFieldValue != "":
            embed.add_field(name=":bust_in_silhouette: no role", value=rolelessPlayersFieldValue, inline=True)

        if dataStorage.getGuildData(self.guild, "useSummaryEmbeds"):
            summaryChannel = self.guild.get_channel(dataStorage.getGuildData(self.guild, "summaryChannel"))
            if summaryChannel is not None:
                try:
                    await summaryChannel.send(embed=embed)
                except:
                    pass

        for plr in self.players:
            await objectives.addXP(plr.member, self.day * 5)
            if not self.victory and plr.role.name == "murderer" or not self.victory and plr.role.name == "werewolf" or self.foolWin and plr.role.name == "fool":
                await objectives.addXP(plr.member, 20)
            elif self.victory and plr.role.name != "murderer" and plr.role.name != "werewolf" and plr.role.name != "fool":
                await objectives.addXP(plr.member, 10)
        for plr in self.allPlayers:
            await objectives.checkForCompleteObjectives(plr.member)
            if self.guild != mainGuild:
                if not dataStorage.getPlayerData(plr.member, "promotionalMessageSent", default=False):
                    dataStorage.setPlayerData(plr.member, "promotionalMessageSent", value=True)
                    embed = discord.Embed(title="Thank you for playing Murder Mystery!",
                                          description="If you liked the game, I would highly appreciate if you joined the discord server! There you can also play the game with other people as well as suggest new features, report bugs, and more!\n\nAlso, give the bot a review on top.gg: https://top.gg/bot/590980247801954304\n\nThanks for playing!\n-Murder Mystery's developer",
                                          color=0x00b8ff)
                    embed.set_thumbnail(
                        url="https://cdn.discordapp.com/attachments/554234775590666251/863864464688676884/blue-heart_1f499.png")
                    try:
                        await plr.member.send(embed=embed)
                        await plr.member.send(mainServerInvite)
                    except:
                        pass

        await self.cleanUp()

    # delete all channels, categories, roles, ect... from the current game
    async def cleanUp(self):
        for player in self.players:
            player.inGame = False
            if not self.guild.id in allPlayers:
                allPlayers[self.guild.id] = []
            if player in allPlayers[self.guild.id]:
                allPlayers[self.guild.id].remove(player)
        try:
            await self.role.delete()
        except:
            pass

        try:
            await self.spectatorRole.delete()
        except:
            pass

        for channel in self.channels:
            try:
                await channel.delete()
            except:
                pass
        try:
            await self.category.delete()
        except:
            pass
        if self in currentGames[self.guild.id]:
            currentGames[self.guild.id].remove(self)
        if self in availableGames[self.guild.id]:
            availableGames[self.guild.id].remove(self)


class player:
    def __init__(self, member, game):
        self.member = member
        self.game = game

        self.inGame = True

        self.voted = False
        self.votes = 0

        self.gold = 1
        self.inventory = []

        self.inJail = False

        self.whisperingTo = []

        self.inLove = False
        self.lover = None
        self.loveChannel = None
        self.dyingNow = False

    def setRole(self, roleName):
        self.role = role(self, roleName)

    async def updateInventory(self):
        if hasattr(self, "inventoryChannel"):
            usableData = []
            for item in self.inventory:
                foundItem = False
                for v in usableData:
                    if v[0].id == item.id:
                        foundItem = True
                        v[1] = v[1] + 1

                if not foundItem:
                    usableData.append([item, 1])

            embed = discord.Embed(title=f"Inventory",
                                  description="Here's a list of all the items you currently own. To buy more items, use !shop at night time.",
                                  color=0x00b8ff)
            for v in usableData:
                if v[0].autoActivate:
                    embed.add_field(name=f"x{v[1]} {v[0].name}",
                                    value=f"{v[0].description}\nThis item will activate automatically",
                                    inline=False)
                else:
                    embed.add_field(name=f"x{v[1]} {v[0].name}",
                                    value=f"{v[0].description}\nUsage: {v[0].usage}",
                                    inline=False)

            await self.inventoryChannel.purge(limit=5)
            await self.inventoryChannel.send(embed=embed)
        else:
            self.inventoryChannel = await self.game.category.create_text_channel("Inventory")
            await self.inventoryChannel.set_permissions(self.game.role, read_messages=False, send_messages=False)
            await self.inventoryChannel.set_permissions(self.member, read_messages=True, send_messages=False)
            self.game.channels.append(self.inventoryChannel)
            await self.updateInventory()


# in-game commands


@client.command()
async def whisper(ctx, member: discord.Member):
    player = getPlayer(ctx.author, ctx.message.guild)
    whisperPlayer = getPlayer(member, ctx.message.guild)
    if player is not None:
        if player.inGame:
            if whisperPlayer is not None:
                if whisperPlayer.inGame:
                    if whisperPlayer in player.game.players:
                        if ctx.channel == player.game.mainChannel:
                            if whisperPlayer not in player.whisperingTo:
                                if len(
                                        f"Whisper between {player.member.display_name} and {whisperPlayer.member.display_name}") < 100:
                                    channel = await player.game.category.create_text_channel(
                                        f"Whisper between {player.member.display_name} and {whisperPlayer.member.display_name}")
                                else:
                                    channel = await player.game.category.create_text_channel("Whisper")
                                player.game.channels.append(channel)
                                player.game.channelsRemoveByNight.append(channel.id)
                                player.whisperingTo.append(whisperPlayer)
                                whisperPlayer.whisperingTo.append(player)
                                await channel.set_permissions(player.game.role, read_messages=False,
                                                              send_messages=False)
                                await channel.set_permissions(player.member, read_messages=True, send_messages=True)
                                await channel.set_permissions(whisperPlayer.member, read_messages=True,
                                                              send_messages=True)

                                await ctx.send(embed=discord.Embed(
                                    title=f"{player.member.display_name} and {whisperPlayer.member.display_name} are now whispering",
                                    description="A private channel only for them has been created, they can talk to each other there. That channel will be deleted once it becomes night.",
                                    color=0x0088ff))
                            else:
                                await ctx.send(":x: You're already whispering to that player!")
                        else:
                            await ctx.send(":x: You can't use that here!")
                    else:
                        await ctx.send(":x: That player is not in the same game as you!")
                else:
                    await ctx.send(":x: That player is not in game!")
            else:
                await ctx.send(":x: Failed to find that player!")
        else:
            await ctx.send(":x: You're not in a game!")
    else:
        await ctx.send(":x: You're not in a game!")


@client.command()
async def vote(ctx, votedMember: discord.Member):
    if not ctx.guild.id in allPlayers:
        currentGames[ctx.guild.id] = allPlayers

    player = getPlayer(ctx.author, ctx.message.guild)
    votedPlayer = getPlayer(votedMember, ctx.message.guild)
    if player is not None:
        if player.inGame:
            if votedPlayer is not None:
                if votedPlayer.inGame:
                    if votedPlayer.game == player.game:
                        if ctx.message.channel == player.game.mainChannel:
                            if player.game.voteTime:
                                if votedPlayer != player:
                                    if not player.voted:
                                        votedPlayer.votes = votedPlayer.votes + 1
                                        player.voted = True
                                        player.votedOn = votedPlayer
                                        await ctx.send(
                                            f"{ctx.author.mention} voted to execute {votedPlayer.member.mention}! They're now at **{votedPlayer.votes}** votes.")
                                        player.game.playersThatVoted.append(player)

                                        player.game.extendVotingTime = True
                                    else:
                                        if player.votedOn != votedPlayer:
                                            player.votedOn.votes -= 1
                                            votedPlayer.votes += 1
                                            await ctx.send(
                                                f"{ctx.author.mention} changed their vote from {player.votedOn.member.mention} to {votedPlayer.member.mention}! They're now at **{votedPlayer.votes}** votes.")
                                            player.votedOn = votedPlayer

                                            player.game.extendVotingTime = True
                                        else:
                                            await ctx.send(":x: You already voted on that player!")
                                else:
                                    await ctx.send(":x: You can't vote on yourself!")
                            else:
                                await ctx.send(":x: You can't vote yet!")
                        else:
                            await ctx.send(":x: You can't use that here!")
                    else:
                        await ctx.send(":x: That player is not in the same game as you!")
                else:
                    await ctx.send(":x: That player is not in game!")
            else:
                await ctx.send(":x: That player is not in game!")
        else:
            await ctx.send(":x: You can only use this command while you're in game!")
    else:
        await ctx.send(":x: You can only use this command while you're in game!")


@client.command()
async def use(ctx, itemName="", *, arg=None):
    if itemName != "":
        player = getPlayer(ctx.author, ctx.message.guild)
        if player is not None:
            for item in player.inventory:
                if item.id == itemName.lower():
                    if arg is not None and item.needArg:
                        if item.needPlayerArg:
                            try:
                                converter = commands.MemberConverter()
                                memberArg = await converter.convert(ctx, arg)
                                playerArg = getPlayer(memberArg, ctx.message.guild)
                            except commands.MemberNotFound:
                                await ctx.message.channel.send(":x: I can't find that player!")
                            except:
                                await ctx.message.channel.send(":x: an unknown error occurred!")
                                raise
                            else:
                                await item.use(ctx, playerArg)
                        else:
                            await item.use(ctx, arg)
                    elif not item.needArg:
                        await item.use(ctx, arg)
                    else:
                        if item.needPlayerArg:
                            await ctx.send(f"Usage: {item.usage}")
                        else:
                            await ctx.send(f"Usage: {item.usage}")
                    break
                # is last in list
                if player.inventory[-1] == item:
                    if hasattr(player, "inventoryChannel"):
                        await ctx.send(embed=discord.Embed(tittle=":x: Couldn't find that item in your inventory!",
                                                           description=f"Make sure you spelled it correctly. You can view your inventory here: {player.inventoryChannel.mention}"))
                    else:
                        await ctx.send(embed=discord.Embed(tittle=":x: Couldn't find that item in your inventory!",
                                                           description=f"Make sure you spelled it correctly."))
        else:
            await ctx.send(":x: You can only do this if you're in a game!")

    else:
        await ctx.send("Usage: !use [item] [@username/text]")


@client.command()
async def shop(ctx):
    player = getPlayer(ctx.author, ctx.message.guild)
    if player is not None:
        if player.inGame:
            if player.game.nightTime:
                if ctx.message.channel == player.nightChannel:
                    embed = discord.Embed(title=f"Shop | :coin: {player.gold}",
                                          description="To buy something, use !buy [item].\nSome item names have spaces in them, the name that you need to enter to buy/use them is in [].",
                                          color=0x00b8ff)
                    broadcasterInGame = False
                    for p in player.game.players:
                        if p.role.name == "broadcaster":
                            broadcasterInGame = True
                    for itemClass in items.getItems(broadcasterInGame=broadcasterInGame, role=player.role.name):
                        item = itemClass()
                        embed.add_field(name=f"{item.name}",
                                        value=f"{item.description}\nCost: :coin: {item.cost}\nTo buy, type !buy {item.id}",
                                        inline=False)
                    await ctx.send(embed=embed)
            else:
                await ctx.send(":x: You can only use this command during :full_moon: night time!")
        else:
            await ctx.send(":x: You can only use this command while you're in a game!")
    else:
        await ctx.send(":x: You can only use this command while you're in a game!")


@client.command(aliases=["money", "gold", "bal"])
async def balance(ctx):
    player = getPlayer(ctx.author, ctx.message.guild)
    if player is not None:
        if player.inGame:
            await ctx.send(f":coin: You have {player.gold} gold.")
        else:
            await ctx.send(":x: You can only use this command while you're in a game!")
    else:
        await ctx.send(":x: You can only use this command while you're in a game!")


@client.command()
async def buy(ctx, itemId):
    player = getPlayer(ctx.author, ctx.message.guild)
    if player is not None:
        if player.inGame:
            if player.game.nightTime:
                if ctx.message.channel == player.nightChannel:
                    itemFound = False
                    broadcasterInGame = False
                    for p in player.game.players:
                        if p.role.name == "broadcaster":
                            broadcasterInGame = True
                    for itemClass in items.getItems(broadcasterInGame=broadcasterInGame, role=player.role.name):
                        if itemClass().id == itemId.lower():
                            itemFound = True
                            await items.buy(itemClass(), player, ctx.message.channel)
                            break
                    if not itemFound:
                        await ctx.send(embed=discord.Embed(title=":x: That's not a valid item!",
                                                           description="Make sure you spelled it correctly!",
                                                           color=0x0000f))
                else:
                    await ctx.send(f":x: You can only use this command in {player.nightChannel.mention}")
            else:
                await ctx.send(":x: You can only use this command during :full_moon: night time!")
        else:
            await ctx.send(":x: You can only use this command while you're in a game!")
    else:
        await ctx.send(":x: You can only use this command while you're in a game!")


@client.command()
async def leave(ctx):
    player = getPlayer(ctx.author, ctx.message.guild)
    if player is not None:
        if player.inGame:
            if not player.game.started:
                await player.game.mainChannel.send(
                    embed=discord.Embed(title=f":heavy_minus_sign: {player.member.display_name} left the game",
                                        color=0xff0000))
            else:
                if not player.game.nightTime:
                    await player.game.mainChannel.send(
                        embed=discord.Embed(title=f":heavy_minus_sign: {player.member.display_name} left the game",
                                            description=f"{player.member.display_name}{player.role.deadString}",
                                            color=0xff0000))
                else:
                    await player.game.sendToAllNightChannels(
                        embed=discord.Embed(title=f":heavy_minus_sign: {player.member.display_name} left the game",
                                            description=f"{player.member.display_name}{player.role.deadString}",
                                            color=0xff0000))
            await player.game.removePlayer(player)
        else:
            await ctx.send(":x: You can only use this command while you're in a game!")
    else:
        await ctx.send(":x: You can only use this command while you're in a game!")


# admin game commands

@client.command()
async def startGame(ctx, indexStr):
    if await permissions.hasPermission(ctx, "admin.game.startGame"):
        index = int(indexStr)
        if index <= len(currentGames[ctx.guild.id]) - 1:
            currentGames[ctx.guild.id][index].startNow = True
            await ctx.send(
                f"Game with index **{indexStr}** should start now if it was in a countdown, or will skip the countdown once it starts counting down!")
        else:
            await ctx.send(":x: There's no game with that ID! use !list to view all games.")


@client.command()
async def skipVotes(ctx, indexStr):
    if await permissions.hasPermission(ctx, "debug.game.skipVotes"):
        index = int(indexStr)
        if index <= len(currentGames[ctx.guild.id]) - 1:
            currentGames[ctx.guild.id][index].skipVotingTime = True
            await ctx.send(
                f"Game with index **{indexStr}** has skipped the voting time!")
        else:
            await ctx.send(":x: There's no game with that index!")


@client.command()
async def skipNight(ctx, indexStr):
    if await permissions.hasPermission(ctx, "debug.game.skipNight"):
        index = int(indexStr)
        if index <= len(currentGames[ctx.guild.id]) - 1:
            currentGames[ctx.guild.id][index].skipNight = True
            await ctx.send(
                f"Game with index **{indexStr}** has skipped night!")
        else:
            await ctx.send(":x: There's no game with that index!")


@client.command()
async def setWeather(ctx, indexStr, num):
    if await permissions.hasPermission(ctx, "debug.game.setWeather"):
        index = int(indexStr)
        if index <= len(currentGames[ctx.guild.id]) - 1:
            currentGames[ctx.guild.id][index].weatherIntensity = int(num)
            await ctx.send(
                f"Game with index **{indexStr}**'s weather has been set to **{num}**!")
        else:
            await ctx.send(":x: There's no game with that index!")


@client.command()
async def setMoon(ctx, indexStr, num):
    if await permissions.hasPermission(ctx, "debug.game.setMoon"):
        index = int(indexStr)
        if index <= len(currentGames[ctx.guild.id]) - 1:
            currentGames[ctx.guild.id][index].moon = int(num)
            await ctx.send(
                f"Game with index **{indexStr}**'s moon has been set to **{num}**!")
        else:
            await ctx.send(":x: There's no game with that index!")


@client.command()
async def kick(ctx, member: discord.Member):
    if await permissions.hasPermission(ctx, "admin.game.kick"):
        player = getPlayer(member, ctx.guild)
        if player is not None:
            if player.inGame:
                if not player.game.started:
                    await player.game.mainChannel.send(
                        embed=discord.Embed(
                            title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game by {ctx.author.display_name}",
                            color=0xff0000))
                else:
                    if not player.game.nightTime:
                        await player.game.mainChannel.send(
                            embed=discord.Embed(
                                title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game by {ctx.author.display_name}",
                                description=f"{player.member.display_name}{player.role.deadString}",
                                color=0xff0000))
                    else:
                        await player.game.sendToAllNightChannels(
                            embed=discord.Embed(
                                title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game by {ctx.author.display_name}",
                                description=f"{player.member.display_name}{player.role.deadString}",
                                color=0xff0000))
                try:
                    await member.send(
                        embed=discord.Embed(title=f"You got kicked out of the game by {ctx.author.display_name}!",
                                            color=0xff0000))
                except:
                    pass
                await player.game.removePlayer(player)
            else:
                await ctx.send(embed=discord.Embed(title="That player is not in game!", color=0xff0000))
        else:
            await ctx.send(embed=discord.Embed(title="I can't find that player in any game!", color=0xff0000))


# some functions

def randomizeList(l):
    random.shuffle(l)
    return l


def getKeys(d):
    l = []
    for v in d:
        l.append(v)
    return l


def getPlayer(member, guild):
    if guild.id not in allPlayers:
        allPlayers[guild.id] = []
    for player in allPlayers[guild.id]:
        if player.member == member:
            return player
    return None


async def createNewGame(guild, debug):
    newGame = game(guild, debug)
    await newGame.createGame()

    return newGame


# stuff

@client.event
async def on_message(message):
    if not isinstance(message.channel, discord.channel.DMChannel):
        if not message.guild.id in allPlayers:
            allPlayers[message.guild.id] = []
        player = getPlayer(message.author, message.guild)
        if player is not None:
            if player.inGame == True:
                if hasattr(player, "roleChannel"):
                    if message.channel == player.roleChannel:
                        await player.role.processRoleChannelCommand(message)

                if hasattr(player, "satelliteChannel"):
                    if message.channel == player.satelliteChannel:
                        await player.currentSatellite.processMessage(message)

                if hasattr(player, "daggerChannel"):
                    if message.channel == player.daggerChannel:
                        await player.currentDagger.processMessage(message)

        if dataStorage.getGuildData(message.guild, "useJoinChannel"):
            if message.channel == message.guild.get_channel(dataStorage.getGuildData(message.guild, "joinChannel")):
                if message.content != "!join":
                    if message.author != client.user:
                        await message.delete()

        if message.author.id == dataStorage.getGuildData(message.guild, "setupMember"):
            if message.channel.id == dataStorage.getGuildData(message.guild, "setupChannel"):
                if dataStorage.getGuildData(message.guild, "awaitingSetupMessage", default=False):
                    await setup.processSetupMessage(message)

        if not dataStorage.getGuildData(message.guild, "setupFinished", default=False):
            if message.content.lower().strip().startswith("!"):
                if message.content.lower().strip() != "!setup":
                    await message.channel.send(
                        embed=discord.Embed(title=":x: Please run !setup before using any commands!",
                                            description="An admin has to use !setup before this bot can execute any commands.",
                                            color=0xff0000))
                else:
                    await client.process_commands(message)
        else:
            await client.process_commands(message)


@client.event
async def on_guild_join(guild):
    channels = guild.channels
    selected = None
    if guild.system_channel is not None:
        if guild.system_channel.permissions_for(guild.get_member(client.user.id)).send_messages:
            selected = guild.system_channel
    if selected is None:
        if guild.public_updates_channel is not None:
            if guild.public_updates_channel.permissions_for(guild.get_member(client.user.id)).send_messages:
                selected = guild.public_updates_channel
    if selected is None:
        for v in channels:
            if isinstance(v, discord.TextChannel):
                if "general" in v.name or "chat" in v.name:
                    if v.permissions_for(guild.get_member(client.user.id)).send_messages:
                        selected = v
    if selected is None:
        for v in channels:
            if isinstance(v, discord.TextChannel):
                if v.permissions_for(guild.get_member(client.user.id)).send_messages:
                    selected = v
    if selected is not None:
        embed = discord.Embed(title="Thanks for inviting Murder Mystery!",
                              description="This bot brings a full on murder mystery game inside of discord!",
                              color=0x00b8ff)
        embed.add_field(name="""Before you can use this bot, type "!setup" """,
                        value="""A few things need to be setup before you can use this bot. Please use "!setup" to set up the bot.""",
                        inline=False)
        embed.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/554234775590666251/819314838949462017/waving-hand_1f44b.png")
        try:
            await selected.send(embed=embed)
        except:
            pass
    print(f"Joined new guild with ID {guild.id} and {guild.member_count} members")


@client.event
async def on_member_update(before, after):
    if after.status == discord.Status.offline:
        if dataStorage.getGuildData(after.guild, "kickOfflinePlayers", default=True):
            player = getPlayer(after, after.guild)
            if player is not None:
                if player.inGame:
                    if not player.game.started:
                        await player.game.mainChannel.send(
                            embed=discord.Embed(
                                title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game because they went offline",
                                color=0xff0000))
                    else:
                        if not player.game.night:
                            await player.game.mainChannel.send(
                                embed=discord.Embed(
                                    title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game because they went offline",
                                    description=f"{player.member.display_name}{player.role.deadString}",
                                    color=0xff0000))
                        else:
                            await player.game.sendToAllNightChannels(
                                embed=discord.Embed(
                                    title=f":heavy_minus_sign: {player.member.display_name} got kicked out of the game because they went offline",
                                    description=f"{player.member.display_name}{player.role.deadString}",
                                    color=0xff0000))
                    try:
                        await after.send(
                            embed=discord.Embed(title="You got kicked out of the game because you went offline!",
                                                description="When you change your status to offline, you automatically get kicked so the game doesn't get filled with AFK people.",
                                                color=0xff0000))
                    except:
                        pass
                    await player.game.removePlayer(player)


@client.event
async def on_member_join(member):
    if member.guild == mainGuild:
        embed = discord.Embed(title=f"{member.display_name} just joined!",
                              description=f"Welcome {member.mention}, have fun with playing Murder Mystery!",
                              color=0x00ff00)
        embed.set_thumbnail(url=member.avatar_url)
        await welcomeChannel.send(embed=embed)

        embed = discord.Embed(title="Welcome to Murder Mystery!",
                              description="Murder Mystery is a game of murder mystery inside discord using a custom discord bot.",
                              color=0x00b8ff)
        embed.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/554234775590666251/819314838949462017/waving-hand_1f44b.png")
        embed.add_field(name="About the game",
                        value="When a game starts, everyone gets assigned an in-game role. These roles have different abilities that can be used in the night. One of those roles is the murderer who can kill 1 person per night. The goal of the game is to find this person, convince other people that they're the murderer, and then vote to execute them during the day. If you are the murderer, then your goal is to kill everyone before they find out it's you.\nEvery few minutes the game cycles between day and night. During the day you can vote to execute on who you think is the murderer. The player with the most votes will get executed.")
        embed.add_field(name="Getting started",
                        value=f"Before playing the game, you should read the rules in {rulesChannel.mention} and read the tutorial in {gameTutorialChannel.mention}, {rolesTutorialChannel.mention}, {itemsTutorialChannel.mention} and {commandsTutorialChannel.mention}.",
                        inline=False)
        embed.add_field(name="Joining a game",
                        value=f"""To join a game simply type "!join" in {joiningChannel.mention}""", inline=False)
        embed.add_field(name="Have fun!", value="Thank you for playing Murder Mystery and have fun!")
        try:
            await member.send(embed=embed)
        except:
            pass


@client.event
async def on_member_remove(member):
    if member.guild == mainGuild:
        embed = discord.Embed(title=f"{member.display_name} just left.", description=f"Goodbye {member.display_name}!",
                              color=0xff0000)
        embed.set_thumbnail(url=member.avatar_url)
        await welcomeChannel.send(embed=embed)



@client.command()
async def showAllRunningGames(ctx):
    if ctx.guild == mainGuild:
        if ctx.message.author.guild_permissions.administrator:
            result = {}
            for v in currentGames:
                result[v] = []
                for g in currentGames[v]:
                    result[v].append([len(g.players), g.started, g.day])
            await ctx.send(f"{result}")
        else:
            await ctx.send(embed=noPermissionEmbed)
    else:
        await ctx.send(":x: Sorry, but you can't do that here! You probably meant: !list")


@client.command()
async def join(ctx, arg="None"):
    if await permissions.hasPermission(ctx, "member.join"):
        author = ctx.author
        guild = ctx.guild
        channel = ctx.message.channel
        allowedToRunCommandHere = True
        joinChannel = guild.get_channel(dataStorage.getGuildData(ctx.guild, "joinChannel"))
        if not ctx.message.author.guild_permissions.administrator or "-overwriteAdminWarning" in arg:
            if dataStorage.getGuildData(ctx.guild, "useJoinChannel"):
                if joinChannel is not None:
                    if channel.id == dataStorage.getGuildData(ctx.guild, "joinChannel"):
                        try:
                            await ctx.message.delete()
                        except:
                            pass
                    else:
                        await ctx.send(embed=discord.Embed(title=":x: You can't use that command here!",
                                                           description=f"This command can only be used in {joinChannel.mention}",
                                                           color=0xff0000))
                        allowedToRunCommandHere = False
                else:
                    await ctx.send(embed=discord.Embed(title=":arrow_forward: Joining a game!",
                                                       description="Finding a game to join or creating a new one, this might take a few seconds!",
                                                       color=0x00b8ff))
            else:
                if getPlayer(ctx.author, ctx.guild) is None:
                    await ctx.send(embed=discord.Embed(title=":arrow_forward: Joining a game!",
                                                       description="Finding a game to join or creating a new one, this might take a few seconds!",
                                                       color=0x00b8ff))
            if allowedToRunCommandHere:
                isInGame = False
                if not guild.id in allPlayers:
                    allPlayers[guild.id] = []
                player = getPlayer(author, guild)
                if player is not None:
                    if player.inGame:
                        isInGame = True

                if not isInGame:
                    if not isSpectating(author):
                        if not guild.id in availableGames:
                            availableGames[guild.id] = []
                        if author.status != discord.Status.offline or not dataStorage.getGuildData(guild, "kickOfflinePlayers", default=True):
                            if availableGames[guild.id] != []:
                                # sort the games to make the fullest games appear first
                                availableGames[guild.id].sort(reverse=True, key=lambda x: len(x.players))

                                foundGame = False
                                for game in availableGames[guild.id]:
                                    if len(game.players) < dataStorage.getGuildData(ctx.guild, "maxPlayers",
                                                                                    default=30):
                                        await game.addPlayer(author)
                                        foundGame = True
                                        break

                                if foundGame == False:
                                    # create new game because all other games are full
                                    newGame = await createNewGame(guild, False)
                                    await newGame.addPlayer(author)

                            else:
                                # create new game and add the player to it
                                newGame = await createNewGame(guild, False)
                                await newGame.addPlayer(author)

                        else:
                            embed = discord.Embed(title="You can't play if your status is offline!",
                                                  description="Because you automatically get kicked when your status changes to offline, you can't join a game if your status is offline.\n\nPlease change your status to something else and try again!",
                                                  color=0xff0000)
                            if dataStorage.getGuildData(ctx.guild, "useJoinChannel"):
                                await author.send(embed=embed)
                            else:
                                await author.send(embed=embed)
                    else:
                        embed = discord.Embed(title="You can't join a game if you're spectating a game!",
                                              description="Please use !spectate to stop spectating so you can join a game.",
                                              color=0xff0000)
                        if dataStorage.getGuildData(ctx.guild, "useJoinChannel"):
                            await author.send(embed=embed)
                        else:
                            await channel.send(embed=embed)

                else:
                    embed = discord.Embed(title="You are already in a game!",
                                          description="You can't join a game when you're already in a game.",
                                          color=0xff000d)
                    if dataStorage.getGuildData(ctx.guild, "useJoinChannel"):
                        await author.send(embed=embed)
                    else:
                        await channel.send(embed=embed)
        else:
            embed = discord.Embed(title=":warning: You have administrator permissions",
                                  description=f"This game doesn't work well with administrator permissions, because it works by hiding channels of other players from you. With administrator permissions, you can see all channels which ruins the game.\n**Please use an alt account without administrator permissions to play this game.**\n\nUse '{dataStorage.getGuildData(guild, 'prefix', default='!')}join -overwriteAdminWarning' to join anyways.",
                                  color=0xfff100)
            if dataStorage.getGuildData(ctx.guild, "useJoinChannel"):
                try:
                    await author.send(embed=embed)
                except:
                    try:
                        channel.send(embed=embed)
                    except:
                        pass
                try:
                    await ctx.message.delete()
                except:
                    pass
            else:
                await channel.send(embed=embed)


# command for clearing the game channels
@client.command(aliases=["endGames", "endAllGames", "stopGames", "stopAllGames"])
async def cleanup(ctx):
    if await permissions.hasPermission(ctx, "admin.endAllGames"):
        if ctx.message.author.guild_permissions.administrator:
            await ctx.send(":hourglass: Ending all running games, Please wait...")
            if not ctx.guild.id in currentGames:
                currentGames[ctx.guild.id] = []
            while len(currentGames[ctx.guild.id]) >= 1:
                currentGame = currentGames[ctx.guild.id][0]
                await currentGame.cleanUp()
            await ctx.send(":white_check_mark: All games have been stopped!")


@client.command(aliases=["discord", "bug", "reportBug", "report", "suggest", "suggestion", "suggestions"])
async def dc(ctx):
    embed = discord.Embed(title="Join the Murder Mystery discord server",
                          description="Join the Murder Mystery discord server to suggest features, report bugs, or play the game with people!",
                          color=0x00b8ff)
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/attachments/554234775590666251/863863056516513792/dagger_1f5e1-fe0f.png")
    await ctx.send(embed=embed)
    await ctx.send(mainServerInvite)


@client.command(aliases=["stopGame"])
async def endGame(ctx, indexStr=None):
    if await permissions.hasPermission(ctx, "admin.endGame"):
        if not ctx.guild.id in currentGames:
            currentGames[ctx.guild.id] = []
        try:
            index = int(indexStr)
        except ValueError:
            await ctx.send(
                f":x: Please give a game ID! You can find a game id with {dataStorage.getGuildData(ctx.guild, 'prefix', default='!')}list.")
        else:
            if len(currentGames[ctx.guild.id]) > index:
                await ctx.send(f":hourglass: Ending game with ID {index}, please wait...")
                await currentGames[ctx.guild.id][index].cleanup()
                await ctx.send(f":white_check_mark: Game with ID {index} has been ended!")
            else:
                await ctx.send(f":x: There's no game with that index!")


# command for creating a new empty game
@client.command()
async def createGame(ctx, debugStr="False"):
    if await permissions.hasPermission(ctx, "debug.createGame"):
        if debugStr == "False":
            debug = False
        elif debugStr == "True":
            debug = True
        else:
            debug = False

        game = await createNewGame(ctx.message.guild, debug)
        if not debug:
            embed = discord.Embed(title="A new game has successfully been created!",
                                  description=f"with index {currentGames[ctx.guild.id].index(game)}")
        else:
            embed = discord.Embed(title="A new game has been created in debugging mode!",
                                  description=f"with index {currentGames[ctx.guild.id].index(game)}")
        await ctx.send(embed=embed)


@client.command()
async def list(ctx):
    if await permissions.hasPermission(ctx, "member.list"):
        embed = discord.Embed(title="Currently running games",
                              description="Here is a list of all currently running games.\nUse !spectate <ID> to spectate a game.",
                              color=0x0088ff)
        if not ctx.guild.id in currentGames:
            currentGames[ctx.guild.id] = []
        for game in currentGames[ctx.guild.id]:
            playerList = ""
            for player in game.players:
                playerList = playerList + str(player.member.mention)

            if playerList == "":
                playerList = "There are no players in this game"

            embed.add_field(name=f"ID: {currentGames[ctx.guild.id].index(game)}",
                            value=f"Started: {game.started}, day {game.day}, players: {playerList}")

        await ctx.send(embed=embed)


@client.command()
async def spectate(ctx, indexStr=None):
    if await permissions.hasPermission(ctx, "member.spectate"):
        if not isSpectating(ctx.author):
            if indexStr is None:
                if len(currentGames[ctx.guild.id]) == 1:
                    indexStr = "0"
                else:
                    if ctx.channel != joiningChannel:
                        await ctx.send(
                            embed=discord.Embed(title="Please enter a game ID",
                                                description="To get a game ID, type !list.",
                                                color=0xff0000))
            player = getPlayer(ctx.author, ctx.message.guild)
            if player is None:
                index = None
                try:
                    index = int(indexStr)
                except ValueError:
                    if ctx.channel != joiningChannel:
                        await ctx.send(embed=discord.Embed(title=":x: Please enter a number!",
                                                           description="Please enter a game's ID to spectate it. You can get a game's ID with !list.",
                                                           color=0xff0000))
                except:
                    await ctx.send(":x: An unknown error occurred!")
                    raise
                else:
                    if index <= len(currentGames[ctx.guild.id]) - 1:
                        await currentGames[ctx.guild.id][index].addSpectator(ctx.author)
                        if ctx.channel != joiningChannel:
                            await ctx.send(embed=discord.Embed(title="You are now spectating a game",
                                                               description="The game's channel should appear on the top of your channel list.\nTo stop spectating, type !spectate again.",
                                                               color=0x0088ff))
                    else:
                        if ctx.channel != joiningChannel:
                            await ctx.send(embed=discord.Embed(title=":x: That game doesn't exist!",
                                                               description="Please enter a valid game ID. You can get a game's ID with !list."))


            else:
                if ctx.channel != joiningChannel:
                    await ctx.send(embed=discord.Embed(title=":x: You are already in a game!",
                                                       description="You can't spectate a game while you're already in a different game.",
                                                       color=0xff0000))

        else:
            for game in currentGames[ctx.guild.id]:
                if ctx.author in game.spectators:
                    await game.removeSpectator(ctx.author)
                    if ctx.channel != joiningChannel:
                        await ctx.send(embed=discord.Embed(title="You are no longer spectating", color=0x0088ff))


def isSpectating(member: discord.Member):
    if not member.guild.id in currentGames:
        currentGames[member.guild.id] = []
    spectating = False
    for game in currentGames[member.guild.id]:
        if member in game.spectators:
            spectating = True
    return spectating


@client.command()
async def purge(ctx, amount):
    if await permissions.hasPermission(ctx, "admin.purge"):
        await ctx.message.channel.purge(limit=int(amount))


@client.command()
async def giveGold(ctx, member: discord.Member, amount):
    if await permissions.hasPermission(ctx, "admin.game.giveGold"):
        player = getPlayer(member, ctx.message.guild)
        if player is not None:
            if player.inGame:
                intAmount = int(amount)
                player.gold += intAmount
                await ctx.send(f"Gave :coin: {amount} gold to {member.mention}")
            else:
                ctx.send("That player is not in game!")
        else:
            ctx.send("That member is not in game!")


@client.command()
async def skipObjectiveTimer(ctx, member: discord.Member):
    if await permissions.hasPermission(ctx, "debug.objectives.skipObjectiveTimer"):
        setPlayerData(member, "nextObjectiveReceivable", value=datetime.datetime.utcnow().isoformat())
        await ctx.send(f"Skipped objective timer for **{member.display_name}**")


@client.command()
async def resetObjectiveData(ctx, member: discord.Member):
    if await permissions.hasPermission(ctx, "debug.objectives.resetObjectiveData"):
        deletePlayerData(member, "completedObjectives")
        deletePlayerData(member, "objective")
        deletePlayerData(member, "objectiveProgress")
        deletePlayerData(member, "nextObjectiveReceivable")
        deletePlayerData(member, "possibleObjectiveDifficulty")
        await ctx.send("Done!")


@client.command()
async def completeCurrentObjective(ctx, member: discord.Member):
    if await permissions.hasPermission(ctx, "debug.objectives.completeCurrentObjective"):
        await objectives.checkForCompleteObjectives(member, forceComplete=True)
        await ctx.send(f"**{member.mention}**'s objective should be completed now if they had one")


@client.command()
async def giveObjective(ctx, member: discord.Member, index):
    if await permissions.hasPermission(ctx, "debug.objectives.giveObjective"):
        index = int(index)
        if objectives.giveObjective(member, index=index):
            await ctx.send(f"Objective with index **{index}** has been given to **{member.display_name}**")
        else:
            await ctx.send(f":x: There is no objective with index **{index}**!")


# handling errors
@client.event
async def on_command_error(ctx, error):
    if ctx.guild == mainGuild:
        if ctx.channel.id == dataStorage.getGuildData(mainGuild, "joinChannel"):
            sendTo = errorChannel
        else:
            sendTo = ctx.channel
    else:
        sendTo = ctx.channel
    if isinstance(error, commands.MemberNotFound):
        await sendTo.send(embed=discord.Embed(title=":x: That's not a valid player!",
                                              description="Make sure you spelled the username correctly (including capitalization). You can also mention them using @username, or by right clicking their username and pressing mention, or by pressing their username if you're on mobile.",
                                              color=0xff0011))
    elif "NotFound: 404 Not Found" in str(error):
        print(str(error))
        raise error

    elif isinstance(error, commands.MissingRequiredArgument):

        await sendTo.send(embed=discord.Embed(title=":x: You need to include more arguments!",
                                              description="You haven't provided enough arguments for this command.",
                                              color=0xff0000))
    elif isinstance(error, commands.CommandNotFound):
        if not "!d " in ctx.message.content:
            if ctx.channel.id != dataStorage.getGuildData(ctx.guild, "joinChannel"):
                if ctx.guild == mainGuild:
                    await sendTo.send(
                        embed=discord.Embed(title=":x: Invalid command!", description="Make sure you spelled it correctly!",
                                            color=0xff0000))
    elif isinstance(error, ValueError):
        try:
            await sendTo.send(embed=discord.Embed(title=":x: Value error!",
                                                  description=f"{''.join(tb.format_exception(None, error, error.__traceback__))}\n\n\nThis error can be caused by giving the incorrect data type in a command, but it can also be a bug.\nIf you think it's a bug, please report it. Otherwise try giving the correct data type, like a number instead of text.",
                                                  color=0xff0000))
        except:
            print("Failed to send an error")
    else:
        try:
            await sendTo.send(embed=discord.Embed(title=":x: An error occurred!",
                                                  description=f"{''.join(tb.format_exception(None, error, error.__traceback__))}\n\n\nPlease report this error if you think this is a bug.",
                                                  color=0xff0000))
        except:
            print("Failed to send an error")
        raise error


@client.command()
async def send_embeds(ctx, mode):
    joinEmbed = discord.Embed(title="Type !join to join a game!", color=0x0088ff)
    if ctx.guild == mainGuild:
        if ctx.message.author.guild_permissions.administrator:
            if mode == "local":
                embeds = tutorial.getTutorialEmbeds(ctx.guild)
                gameTutorialChannel = ctx.guild.get_channel(dataStorage.getGuildData(ctx.guild, "gameTutorialChannel"))
                itemsTutorialChannel = ctx.guild.get_channel(
                    dataStorage.getGuildData(ctx.guild, "itemsTutorialChannel"))
                rolesTutorialChannel = ctx.guild.get_channel(dataStorage.getGuildData(ctx.guild, "roleTutorialChannel"))
                commandsTutorialChannel = ctx.guild.get_channel(
                    dataStorage.getGuildData(ctx.guild, "commandsTutorialChannel"))
                if gameTutorialChannel is not None and itemsTutorialChannel is not None and rolesTutorialChannel is not None and commandsTutorialChannel is not None:
                    await gameTutorialChannel.purge(10)
                    await itemsTutorialChannel.purge(10)
                    await rolesTutorialChannel.purge(10)
                    await commandsTutorialChannel.purge(10)
                    for v in embeds["game"]:
                        await gameTutorialChannel.send(embed=v)
                    for v in embeds["role"]:
                        await rolesTutorialChannel.send(embed=v)
                    for v in embeds["items"]:
                        await itemsTutorialChannel.send(embed=v)
                    for v in embeds["commands"]:
                        await commandsTutorialChannel.send(embed=v)
                else:
                    await ctx.send(":warning: One of the tutorial channels can't be found!")
                joinChannel = dataStorage.getGuildData(ctx.guild, "joinChannel")
                if joinChannel is not None:
                    await joinChannel.purge(20)
                    await joinChannel.send(embed=joinEmbed)
                else:
                    await ctx.send(":x: One of the tutorial channel IDs returns None!")
                await ctx.send(":white_check_mark: Done!")
            elif mode == "global":
                for v in dataStorage.getAllGuilds():
                    guild = client.get_guild(v)
                    if v["useTutorialChannels"]:
                        if guild is not None:
                            gameTutorialChannel = guild.get_channel(v["gameTutorialChannel"])
                            itemsTutorialChannel = guild.get_channel(v["itemsTutorialChannel"])
                            rolesTutorialChannel = guild.get_channel(v["rolesTutorialChannel"])
                            commandsTutorialChannel = guild.get_channel(v["commandsTutorialChannel"])
                            if gameTutorialChannel is not None and itemsTutorialChannel is not None and rolesTutorialChannel is not None and commandsTutorialChannel is not None:
                                try:
                                    embeds = tutorial.getTutorialEmbeds(guild)
                                    await gameTutorialChannel.purge(10)
                                    await itemsTutorialChannel.purge(10)
                                    await rolesTutorialChannel.purge(10)
                                    await commandsTutorialChannel.purge(10)
                                    for v in embeds["game"]:
                                        await gameTutorialChannel.send(embed=v)
                                    for v in embeds["role"]:
                                        await rolesTutorialChannel.send(embed=v)
                                    for v in embeds["items"]:
                                        await itemsTutorialChannel.send(embed=v)
                                    for v in embeds["commands"]:
                                        await commandsTutorialChannel.send(embed=v)
                                except Exception as error:
                                    await ctx.send(embed=discord.Embed(title=f":warning: Error in guild {guild.id}",
                                                                       description=f"{error}", color=0xfff100))
                            else:
                                await ctx.send(
                                    f":warning: Guild {v} uses tutorial channels, but one of the tutorial channels couldn't be found!")
                    if v["useJoinChannel"]:
                        if guild is not None:
                            joinChannel = guild.get_channel(v["joinChannel"])
                            if joinChannel is not None:
                                try:
                                    await joinChannel.purge(20)
                                    await joinChannel.send(embed=joinEmbed)
                                except Exception as error:
                                    await ctx.send(embed=discord.Embed(title=f":warning: Error in guild {guild.id}",
                                                                       description=f"{error}", color=0xfff100))
                            else:
                                await ctx.send(
                                    f":warning: guild {guild.id} uses the join channel, but no join channel could be found!")
                    await ctx.send(":white_check_mark: Done!")

            else:
                await ctx.send(":x: Please select mode local or global!")


        else:
            await ctx.send(embed=noPermissionEmbed)
    else:
        await ctx.send(":x: Sorry, but you can't use this command in this server.")


client.remove_command("help")


@client.command(aliases=["command", "commands"])
async def help(ctx):
    if await permissions.hasPermission(ctx, "member.help"):
        for v in tutorial.getTutorialEmbeds(ctx.guild)["commands"]:
            await ctx.send(embed=v)


@client.command()
async def advancedHelp(ctx, category=None):
    if await permissions.hasPermission(ctx, "member.help"):
        p = dataStorage.getGuildData(ctx.guild, "prefix", default="!")
        if category is None:
            embed = discord.Embed(title="Advanced help", color=0x00b8ff)
            embed.add_field(name=":adult: Member",
                            value=f"{p}advancedHelp member - views all commands under the member permission group.",
                            inline=False)
            embed.add_field(name=":video_game: Game",
                            value=f"{p}advancedHelp game - views all commands usable during game.", inline=False)
            embed.add_field(name=":person_in_tuxedo: Admin",
                            value=f"{p}advancedHelp admin - views all commands under the admin permission group.",
                            inline=False)
            embed.add_field(name=":scroll: Debug",
                            value=f"{p}advancedHelp debug - views all commands under the debug permission group.",
                            inline=False)
            await ctx.send(embed=embed)
        elif category == "member":
            embed = discord.Embed(title="Advanced help - :adult: Member",
                                  description="Arguments in <> are required, arguments in [] are optional",
                                  color=0x00b8ff)
            embed.add_field(name=f"{p}help", value="permission: member.help\n\nViews a list of all simple commands",
                            inline=False)
            embed.add_field(name=f"{p}advancedHelp [category]",
                            value="permission: member.help\n\nViews a list of all commands", inline=False)
            embed.add_field(name=f"{p}join",
                            value="permission: member.join\n\nJoins a game. Depending on how the server is configured, this command might only be usable in a join channel.",
                            inline=False)
            embed.add_field(name=f"{p}spectate [id]",
                            value="permission: member.spectate\n\nSpectates a game. If only one game is running, no ID has to be given.",
                            inline=False)
            embed.add_field(name=f"{p}list",
                            value="permission: member.list\n\nShows all currently running games and their IDs",
                            inline=False)
            embed.add_field(name=f"{p}level [player]",
                            value="permission: member.levels.level\n\nShows the player's level", inline=False)
            embed.add_field(name=f"{p}objective",
                            value="permission: member.levels.objective\n\nShows your current objective progress or gives you a new one",
                            inline=False)
            embed.add_field(name=f"{p}stats [player]",
                            value="permission: member.levels.stats\n\nShows your or the player's stats", inline=False)
            embed.add_field(name=f"{p}prefix",
                            value="permission: no permissions needed\n\nShows the bot's prefix for this server",
                            inline=False)
            await ctx.send(embed=embed)
        elif category == "admin":
            embed = discord.Embed(title="Advanced help - :person_in_tuxedo: Admin",
                                  description="Arguments in <> are required, arguments in [] are optional",
                                  color=0x00b8ff)
            embed.add_field(name=f"{p}purge <number>",
                            value="permission: admin.purge\n\nDeletes the last <number> amount of messages",
                            inline=False)
            embed.add_field(name=f"{p}endAllGames",
                            value=f"permission: admin.endAllGames\n\nEnds all currently running games. {p}cleanup does the same.",
                            inline=False)
            embed.add_field(name=f"{p}endGame <game ID>",
                            value=f"permission: admin.endGame\n\nEnds the game with the specified ID. Game IDs can be obtained with {p}list",
                            inline=False)
            embed.add_field(name=f"{p}setup", value="permission: admin.setup\n\nReruns the setup", inline=False)
            embed.add_field(name=f"{p}settings [setting] [value]",
                            value="permission: admin.settings\n\nSet different kind of settings on how the game behaves, like the amount of players needed to start a game, how long night time takes, ect...",
                            inline=False)
            embed.add_field(name=f"{p}prefix [new prefix]",
                            value="permission: admin.prefix\n\nShows the bot's current prefix or sets a new one. Members can run this command as well but can't change the prefix without the admin.prefix permission.",
                            inline=False)
            embed.add_field(name=f"{p}addPermission <member/role> <permission>",
                            value="permission: admin.permissions.addPermission\n\nAdds a permission to a role or member",
                            inline=False)
            embed.add_field(name=f"{p}removePermission <member/role> <permission>",
                            value="permission: admin.permissions.removePermissions\n\nRemoves a permission from a role or member",
                            inline=False)
            embed.add_field(name=f"{p}permissions [member/role]",
                            value="permission: admin.permissions\n\nViews the permissions for the member/role. If no argument is given, it will show all possible permissions.",
                            inline=False)
            embed.add_field(name=f"{p}giveGold <player> <amount>",
                            value="permission: admin.game.giveGold\n\nGives the specified player the specified amount of extra gold in game",
                            inline=False)
            embed.add_field(name=f"{p}kick <player>",
                            value="permission: admin.game.kick\n\nKicks the specified player out of the game",
                            inline=False)
            embed.add_field(name=f"{p}startGame <game ID>",
                            value="permission: admin.game.startGame\n\nSkips the pre-game timer", inline=False)
            await ctx.send(embed=embed)
        elif category == "debug":
            embed = discord.Embed(title="Advanced help - :scroll: Debug",
                                  description="Arguments in <> are required, arguments in [] are optional\n\n**These are advanced commands not meant to be used. Feel free to mess around, but most of these commands were made for debugging purposes and will be confusing if you don't have the source code**\n\n**:warning: Some of these commands could break the bot if used incorrectly :warning:**",
                                  color=0x00b8ff)
            embed.add_field(name=f"{p}createGame [True/False]",
                            value=f"permission: debug.createGame\n\nCreates an empty game. If you want to start the game in debugging mode, use {p}createGame True.",
                            inline=False)
            embed.add_field(name=f"{p}addObjectiveProgress <member> <task> <value>",
                            value=f"permission: debug.objectives.addObjectiveProgress\n\nAdds objective progress",
                            inline=False)
            embed.add_field(name=f"{p}giveObjective <member> <index>",
                            value="permission: debug.objectives.giveObjective\n\nSets the objective of the player to the specified index.\n:warning: Unexpected behaviour might occur if set to an invalid index.",
                            inline=False)
            embed.add_field(name=f"{p}completeCurrentObjective <member>",
                            value="permission: debug.objectives.completeCurrentObjective\n\nCompletes the specified member's current objective",
                            inline=False)
            embed.add_field(name=f"{p}skipObjectiveTimer <member>",
                            value="permission: debug.objectives.skipObjectiveTimer\n\nSkips the in-between objective timer of the specified member",
                            inline=False)
            embed.add_field(name=f"{p}setMoon <game ID> <brightness (1-5)",
                            value="permission: debug.game.setMoon\n\nSets the moon brightness in the specified game. 1 is no moon, 5 is full moon.\nPlease only use this command after the weather forecast, and before night time starts",
                            inline=False)
            embed.add_field(name=f"{p}setWeather <game ID> <intensity (0-99)>",
                            value="permission: debug.game.setWeather\n\nSets the weather intensity. 0 for not intense and 99 for very intense.\nPlease only use this command after the weather forecast, and before night time starts",
                            inline=False)
            embed.add_field(name=f"{p}skipNight <game ID>", value="permission: debug.game.skipNight\n\nSkips the night",
                            inline=False)
            embed.add_field(name=f"{p}skipVotes <game ID>",
                            value="permission: debug.game.skipVotes\n\nSkips voting time", inline=False)
            await ctx.send(embed=embed)
        elif category == "game":
            embed = discord.Embed(title="Advanced help - :video_game Game",
                                  description="Arguments in <> are required, arguments in [] are optional\nThe commands below do not have any permission settings, because they can only be used in game which can only be accessed with the permission 'member.join'.",
                                  color=0x00b8ff)
            embed.add_field(name=f"{p}vote <player>", value="Vote on the specified player to be executed during game",
                            inline=False)
            embed.add_field(name=f"{p}shop", value="Views all shop items", inline=False)
            embed.add_field(name=f"{p}buy <item>", value="Buy an item from the shop", inline=False)
            embed.add_field(name=f"{p}use <item> [argument]",
                            value="Use an item. The argument being required or not depends on the item.", inline=False)
            embed.add_field(name=f"{p}whisper <player>",
                            value="Creates a private channel for the command runner and the specified player to talk in",
                            inline=False)
            embed.add_field(name=f"{p}leave", value="Leaves the game")
            await ctx.send(embed=embed)


@client.command(aliases=["setting", "options", "option", "config", "configuration"])
async def settings(ctx, setting=None, value=None):
    if await permissions.hasPermission(ctx, "admin.settings"):
        if setting is None:
            if dataStorage.getGuildData(ctx.guild, "gameVoiceChannel", default=False):
                voiceChannelValueSting = "Yes"
            else:
                voiceChannelValueSting = "No"
            if dataStorage.getGuildData(ctx.guild, "lockVoiceChannelDuringNight", default=False):
                voiceChannelLockString = "Yes"
            else:
                voiceChannelLockString = "No"
            if dataStorage.getGuildData(ctx.guild, "kickOfflinePlayers", default=True):
                kickOfflinePlayersString = "Yes"
            else:
                kickOfflinePlayersString = "No"
            await ctx.send(embed=discord.Embed(title="List of settings",
                                               description=f"minPlayers:{dataStorage.getGuildData(ctx.guild, 'minPlayers')}\nSet the minimal amount of players required to start a game\n\nmaxPlayers: {dataStorage.getGuildData(ctx.guild, 'maxPlayers')}\nSet the maximum players that can fit in a game\n\npreGameTimer: {dataStorage.getGuildData(ctx.guild, 'preGameTimer')}\nSets the amount of time in seconds that it takes for a game to start when the game has enough players\n\nvotingTime: {dataStorage.getGuildData(ctx.guild, 'votingTime')}\nSets the amount of time in seconds that it takes before voting time ends\n\nnightTimeTimer: {dataStorage.getGuildData(ctx.guild, 'nightTimeTimer')}\nSets the amount of time in seconds that it takes for night time to end\n\nUse !settings <setting> <value> to set a setting\nThe settings below don't need a value, but they will change from 'yes' to 'no' with !settings <setting>\n\nvoiceChannel: {voiceChannelValueSting}\nCreate a voice channel for the game when a game is created\n\nlockVoiceChannelDuringNight: {voiceChannelLockString}\nLocks the game's voice channel when it becomes night (needs voiceChannel to be set to 'yes')\n\nkickOfflinePlayers: {kickOfflinePlayersString}\nKicks players out of a game when they go offline\n\n\nTo set permissions, use !permissions and !setPermissions\nTo change the bot's prefix, use !prefix",
                                               color=0x00b8ff))
        else:
            if setting.lower().strip() == "voicechannel":
                if dataStorage.getGuildData(ctx.guild, "gameVoiceChannel", default=False):
                    dataStorage.setGuildData(ctx.guild, "gameVoiceChannel", value=False)
                    await ctx.send(embed=discord.Embed(
                        title=":white_check_mark: The bot will no longer make a voice channel for a game when the game is created!",
                        color=0x00ff00))
                else:
                    dataStorage.setGuildData(ctx.guild, "gameVoiceChannel", value=True)
                    await ctx.send(embed=discord.Embed(
                        title=":white_check_mark: The bot will now make a voice channel for a game when the game is created!",
                        color=0x00ff00))

            elif setting.lower().strip() == "lockvoicechannelduringnight":
                if ctx.guild.get_member(client.user.id).guild_permissions.move_members:
                    if dataStorage.getGuildData(ctx.guild, "lockVoiceChannelDuringNight", default=False):
                        dataStorage.setGuildData(ctx.guild, "lockVoiceChannelDuringNight", value=False)
                        await ctx.send(embed=discord.Embed(
                            title=":white_check_mark: The bot will no longer lock the game's voice channel during the night!",
                            color=0x00ff00))
                    else:
                        dataStorage.setGuildData(ctx.guild, "lockVoiceChannelDuringNight", value=True)
                        await ctx.send(embed=discord.Embed(
                            title=":white_check_mark: The bot will now lock the game's voice channel during the night!",
                            color=0x00ff00))
                else:
                    await ctx.send(embed=discord.Embed(title=":x: This setting requires the permission 'move members'.", description="Please add the permission 'move members' to the bot's role in your server's settings", color=0xff0000))

            elif setting.lower().strip() == "kickofflineplayers":
                if dataStorage.getGuildData(ctx.guild, "kickOfflinePlayers", default=True):
                    dataStorage.setGuildData(ctx.guild, "kickOfflinePlayers", value=False)
                    await ctx.send(embed=discord.Embed(
                        title=":white_check_mark: Offline players will no longer be kicked!",
                        color=0x00ff00))
                else:
                    dataStorage.setGuildData(ctx.guild, "kickOfflinePlayers", value=True)
                    await ctx.send(embed=discord.Embed(
                        title=":white_check_mark: Offline players will now be kicked!",
                        color=0x00ff00))


            else:
                try:
                    intSetting = int(value)
                except TypeError:
                    await ctx.send(":x: Please enter a number after the setting!")
                except ValueError:
                    await ctx.send(":x: Please enter a number after the setting!")
                else:
                    if setting.lower().strip() == "minplayers":
                        if intSetting >= 4:
                            if intSetting < dataStorage.getGuildData(ctx.guild, "maxPlayers"):
                                dataStorage.setGuildData(ctx.guild, "minPlayers", value=intSetting)
                                await ctx.send(f":white_check_mark: The setting minPlayers has been set to {intSetting}")
                            else:
                                await ctx.send(":x: This setting must be lower than the setting 'maxPlayers'!")
                        else:
                            await ctx.send(":x: This setting can't be lower than 4!")
                    elif setting.lower().strip() == "maxplayers":
                        if intSetting >= 4:
                            if intSetting > dataStorage.getGuildData(ctx.guild, "minPlayers"):
                                dataStorage.setGuildData(ctx.guild, "maxPlayers", value=intSetting)
                                await ctx.send(f":white_check_mark: The setting maxPlayers has been set to {intSetting}")
                            else:
                                await ctx.send(":x: This setting must be higher than the setting 'minPlayers'!")
                        else:
                            await ctx.send(":x: This setting can't be lower than 4!")
                    elif setting.lower().strip() == "pregametimer":
                        if intSetting >= 5:
                            dataStorage.setGuildData(ctx.guild, "preGameTimer", value=intSetting)
                            await ctx.send(f":white_check_mark: The setting preGameTimer has been set to {intSetting}")
                        else:
                            await ctx.send(":x: This setting must be higher than 5!")
                    elif setting.lower().strip() == "votingtime":
                        if intSetting >= 5:
                            dataStorage.setGuildData(ctx.guild, "votingTime", value=intSetting)
                            await ctx.send(f":white_check_mark: The setting votingTime has been set to {intSetting}")
                        else:
                            await ctx.send(":x: This setting must be higher than 5!")
                    elif setting.lower().strip() == "nighttimetimer":
                        if intSetting >= 5:
                            dataStorage.setGuildData(ctx.guild, "nightTimeTimer", value=intSetting)
                            await ctx.send(f":white_check_mark: The setting nightTimeTimer has been set to {intSetting}")
                        else:
                            await ctx.send(":x: This setting must be higher than 5!")
                    else:
                        await ctx.send(
                            f":x: '{setting}' is not a valid setting! Use {dataStorage.getGuildData(ctx.guild, 'prefix', default='!')}settings to see a list of all settings")


@client.command(aliases=["prefixes", "setPrefix", "changePrefix", "getPrefix"])
async def prefix(ctx, newValue=None):
    if newValue is None:
        await ctx.send(
            f"My current prefix for this server is: {dataStorage.getGuildData(ctx.guild, 'prefix', default='!')}")
    else:
        if permissions.memberHasPermission(ctx.author, "admin.prefix"):
            if 7 >= len(str(newValue).strip().lower()) > 0:
                dataStorage.setGuildData(ctx.guild, "prefix", value=str(newValue).strip().lower())
                await ctx.send(f":white_check_mark: My prefix has been set to '{str(newValue).strip().lower()}'")
            else:
                await ctx.send(":x: The prefix can't be bigger than 7 characters!")
        else:
            await ctx.send(embed=discord.Embed(title=":closed_lock_with_key: You don't have permission to do that!",
                                               description=f"You're missing the following permission: admin.prefix\n\nIf you think you're supposed to have this permission, then ask an admin to execute the following command:\n!addPermission {ctx.author.mention} {permission}",
                                               color=0xff0000))


@client.command()
async def sendNotificationMessage(ctx):
    if ctx.guild == mainGuild:
        if ctx.message.author.guild_permissions.administrator:
            embed = discord.Embed(title="Notification settings",
                                  description="Here you can change what notifications you want. React the corresponding emojis to what notifications you want.",
                                  color=0x00b8ff)
            embed.add_field(name=":one: Bot updates", value="Get notified whenever the bot updates", inline=False)
            embed.add_field(name=":two: New games", value="Get notified whenever a new game is created", inline=False)
            embed.add_field(name=":three: Games starting",
                            value="Get notified whenever a new game is about to start (so when there are enough players to start and the countdown to starting begins)",
                            inline=False)
            message = await notificationSettingsChannel.send(embed=embed)
            # NOTE: in some editors these might appear like normal numbers, but these are actually emojis.
            await message.add_reaction("1️⃣")
            await message.add_reaction("2️⃣")
            await message.add_reaction("3️⃣")
            global notificationMessage
            notificationMessage = message
            notificationMessageFile = open("notificationMessageID", "w")
            notificationMessageFile.write(str(notificationMessage.id))
            notificationMessageFile.close()
        else:
            await ctx.send(embed=noPermissionEmbed)




@client.command()
async def reloadCache(ctx):
    if ctx.guild == mainGuild:
        if ctx.message.author.guild_permissions.administrator:
            await ctx.send(":hourglass: Reloading cache, please wait...")
            dataStorage.reloadCache()
            await ctx.send(":white_check_mark: Cache has been reloaded!")
        else:
            await ctx.send(embed=noPermissionEmbed)


# I used this when adding !stats to get the stats in the database from the old games. It's commented out but not deleted in case I need it later
"""
@client.command()
async def get_data_from_result_embeds(ctx):
    if ctx.message.author.guild_permissions.administrator:
        messages = [809478299256225792, 809530109534404668, 809533084445179904, 809539497988849744, 809543556796645426,
                    809546628947640350, 809548300645826581, 809551953117708338, 809615182103183380, 809791651441803334,
                    809830171242659890, 809841988135813180, 810251596751306783, 810253910945693737, 810262637174194237,
                    810558325984591892, 810589178990559323, 810591099877195866, 810603500227788860, 810605466773094430,
                    810607671954374687, 810631844185374791, 810728993253752853, 810730945212448828, 810732625362812938,
                    810914246846054490, 810917125886967849, 810919252314816522, 810971851454808095, 810974803661291541,
                    810977617694949417, 810983103098519573, 811020089721094185, 811023344153133096, 811345844322304010,
                    811350417534484480, 811386038881878048, 811724073071149067, 811726959612264509, 811729253719605279,
                    811735553171128370, 811736560898408449, 812035114153803777, 812039264162283540, 812041492478296074,
                    812082187930173441, 812084493568442418, 812095188033339442, 814623570323701830, 814626359556833300,
                    814629805986545675, 814633291163500584, 814635493995970630, 814639817249783869, 814642798564999170,
                    814645835560255488, 814648289193754684, 814650033986732052, 814652772799873055, 814657316418486322,
                    814662026516627507, 814665285817991241, 814669355450761216, 814675242618191934, 814677047087202305,
                    814680377490538567, 814682540145246210, 814685364630847488, 814687548508733460, 814693578256416788,
                    814698285184450610, 814700200492138516, 814706542053556286, 814709206451748874, 814876968666529863,
                    814879345688051743, 814883856985358396, 814886889819209778, 814889092810276864, 814890929864572948,
                    814895128199036950, 814897292338397244, 814904501264711720, 814908490358849576, 814910732281315358,
                    814923724465373204, 814928511961137212, 814930851539189791, 814933998835859566, 814935814609436712,
                    814939284108607488, 814942406168281109, 815241187111338044, 816208691538821120, 816379544965087302,
                    817151667517784125, 818241017810911252]
        channel = client.get_channel(803169209474482190)
        await ctx.send("doing the thing, please wait")
        for msgid in messages:
            msg = await channel.fetch_message(msgid)
            embed = msg.embeds[0]
            victory = True
            if embed.title == "Villagers won!":
                victory = True
            elif embed.title == "Murderer won!":
                victory = False
            else:
                print("Invalid title!")

            for field in embed.fields:
                if field.name != "Game summary":
                    converter = commands.MemberConverter()
                    try:
                        member = await converter.convert(ctx, field.value)
                    except:
                        print("Invalid player!")
                    setPlayerData(member, "gamesPlayed", increase=1)
                    if victory:
                        if field.name != ":dagger: murderer":
                            setPlayerData(member, "villagerWins", increase=1)
                    else:
                        if field.name == ":dagger: murderer":
                            setPlayerData(member, "murdererWins", increase=1)
        await ctx.send("Done!")

    else:
        await ctx.send(embed=noPermissionEmbed)
"""


@client.command()
async def get_player_data(ctx, member: discord.Member, key):
    if await permissions.hasPermission(ctx, "debug.dataStorage.get_player_data"):
        value = getPlayerData(member, key)
        await ctx.send(f"{value}")





# channels & roles
@client.event
async def on_ready():
    global mainGuild, modRole, generalChannel, joiningChannel, rulesChannel, introductionChannel, gameTutorialChannel, rolesTutorialChannel, itemsTutorialChannel, bugChannel, errorChannel, infoChannels, welcomeChannel, commandsTutorialChannel, newGamesRole, gamesStartingRole, botUpdatesRole, nonGameRoles, mainGameRolePosition, notificationSettingsChannel, notificationChannel, notificationMessage, data
    print(f"Logged in as {client.user}")

    mainGuild = None
    if not testingBot:
        mainGuild = client.get_guild(803169209474482187)
        modRole = get(mainGuild.roles, id=809356484252794921)

        generalChannel = client.get_channel(803169209474482190)
        joiningChannel = client.get_channel(863871059367690250)
        rulesChannel = client.get_channel(809086129462706186)
        introductionChannel = client.get_channel(809086505581543514)
        gameTutorialChannel = client.get_channel(809091142040944656)
        rolesTutorialChannel = client.get_channel(809091154675105803)
        itemsTutorialChannel = client.get_channel(809091166950653982)
        commandsTutorialChannel = client.get_channel(809740266298540042)

        notificationSettingsChannel = client.get_channel(810533611228758067)
        notificationChannel = client.get_channel(810533716655865907)

        infoChannels = [joiningChannel, rulesChannel, introductionChannel, gameTutorialChannel, rolesTutorialChannel,
                        itemsTutorialChannel, commandsTutorialChannel]

        bugChannel = client.get_channel(809355113566306355)
        errorChannel = client.get_channel(809098068069974056)
        welcomeChannel = client.get_channel(809466910961696809)

        newGamesRole = get(mainGuild.roles, id=810528878341652490)
        gamesStartingRole = get(mainGuild.roles, id=856300184244846614)
        botUpdatesRole = get(mainGuild.roles, id=810528944456597536)

        nonGameRoles = [newGamesRole, gamesStartingRole, botUpdatesRole]
        # I know this isn't really a good way to do this but it's easy and it works
        notificationMessageFile = None
        try:
            notificationMessageFile = open("notificationMessageID", "r")
            notificationMessage = await notificationSettingsChannel.fetch_message(int(notificationMessageFile.read()))
        except FileNotFoundError:
            print(
                "Notification message ID file not found. If you are not running this bot on the main murder mystery server, then please ignore this.")
        except:
            raise
        finally:
            if notificationMessageFile is not None:
                notificationMessageFile.close()

        mainGameRolePosition = 0
        for r in nonGameRoles:
            if r.position > mainGameRolePosition:
                mainGameRolePosition = r.position
        mainGameRolePosition += 1

    await client.change_presence(status=discord.Status.online, activity=discord.Game(f"!help | !setup | {shortMainServerInvite}"))


def getLen(x):
    l = 0
    for v in x:
        l += 1
    return l


@client.command()
async def stats(ctx, memberArg: discord.Member = None):
    if await permissions.hasPermission(ctx, "member.levels.stats"):
        member = None
        if memberArg is None:
            member = ctx.author
        else:
            member = memberArg

        embed = discord.Embed(title=f"Stats for {member.display_name}",
                              description=f""":video_game: Games played: {getPlayerData(member, "gamesPlayed", default=0)}\n\n:adult: Villager wins: {getPlayerData(member, "villagerWins", default=0)}\n:dagger: Murderer wins: {getPlayerData(member, "murdererWins", default=0)}\n:clown: Fool wins: {getPlayerData(member, "foolWins", default=0)}\n:wolf: Werewolf wins: {getPlayerData(member, "werewolfWins", default=0)}\n""",
                              color=0x00ff00)
        embed.set_thumbnail(url=member.avatar_url)
        embed.add_field(name=f'Level {dataStorage.getPlayerData(member, "level", default=1)}',
                        value=f'{objectives.getXpProgressBar(member)}\n{objectives.getNextLevelRequirement(getPlayerData(member, "level", default=1)) - getPlayerData(member, "xp", default=0)} xp required for next level',
                        inline=True)
        await ctx.send(embed=embed)
        await objectives.addXP(member, 0)


# reaction roles
@client.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is not None:
        member = payload.member
        channel = await client.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        guild = await client.fetch_guild(payload.guild_id)
        if message.id == dataStorage.getGuildData(guild, "setupMessage"):
            if member.id == dataStorage.getGuildData(guild, "setupMember"):
                await setup.processSetupReaction(member, channel, payload.emoji)

        if message == notificationMessage:
            # NOTE: in some editors these might appear like normal numbers, but these are actually emojis.
            if payload.emoji.name == "1️⃣":
                await member.add_roles(botUpdatesRole)
            elif payload.emoji.name == "2️⃣":
                await member.add_roles(newGamesRole)
            elif payload.emoji.name == "3️⃣":
                await member.add_roles(gamesStartingRole)
            else:
                print(payload.emoji.name)


@client.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is not None:
        guild = await client.fetch_guild(payload.guild_id)
        member = await guild.fetch_member(payload.user_id)
        channel = await client.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        if message == notificationMessage:
            # NOTE: in some editors these might appear like normal numbers, but these are actually emojis.
            if payload.emoji.name == "1️⃣":
                await member.remove_roles(botUpdatesRole)
            elif payload.emoji.name == "2️⃣":
                await member.remove_roles(newGamesRole)
            elif payload.emoji.name == "3️⃣":
                await member.remove_roles(gamesStartingRole)
            else:
                print(payload.emoji.name)


@client.command(aliases=["setup"])
async def startSetup(ctx):
    if await permissions.hasPermission(ctx, "admin.setup"):
        gamesRunning = False
        if ctx.guild.id not in currentGames:
            currentGames[ctx.guild.id] = []
        if len(currentGames[ctx.guild.id]) > 0:
            gamesRunning = True
        await setup.initializeSetup(ctx, gamesRunning)


@client.command()
async def purgeInfoChannels(ctx):
    if ctx.guild == mainGuild:
        if ctx.message.author.guild_permissions.administrator:
            for channel in infoChannels:
                await channel.purge(limit=100)


@client.command()
async def addObjectiveProgress(ctx, member, key, value=1):
    if await permissions.hasPermission(ctx, "debug.levels.addObjectiveProgress"):
        if key in objectives.objectiveTasksMeanings:
            objectives.addObjectiveProgress(member, key, value)
        else:
            tasks = ""
            for v in objectives.objectiveTasksMeanings:
                tasks += f"\n{v}"
            await ctx.channel.send(embed=discord.Embed(title=":x: That's not a valid objective task",
                                                       description=f"Objective task options:{tasks}", color=0xff0000))


@client.command(aliases=["objectives", "quest", "quests"])
async def objective(ctx):
    if await permissions.hasPermission(ctx, "member.levels.objective"):
        if getPlayer(ctx.author, ctx.message.guild) is None:
            await objectives.objectivesCommand(ctx)
        else:
            await ctx.send(
                embed=discord.Embed(title=":x: You can't view your objective progress when you're in a game!",
                                    description="""Because some objectives, like "vote on the murderer", can reveal someone's role if the progress increases, you can only check your objective progress outside of games.""",
                                    color=0xff0000))


@client.command(aliases=["xp", "levels"])
async def level(ctx, member: discord.Member = None):
    if await permissions.hasPermission(ctx, "member.levels.level"):
        if member is not None:
            embed = discord.Embed(
                title=f"""{member.display_name} is level {getPlayerData(member, "level", default=1)}""",
                description=f"""{objectives.getXpProgressBar(member)}\n{objectives.getNextLevelRequirement(getPlayerData(member, "level", default=1)) - getPlayerData(member, "xp", default=0)} xp required for next level""",
                color=0x00ff00)
            embed.set_thumbnail(url=member.avatar_url)
            await ctx.send(embed=embed)
            await objectives.addXP(member, 0)
        else:
            embed = discord.Embed(title=f"""You are level {getPlayerData(ctx.author, "level", default=1)}""",
                                  description=f"""{objectives.getXpProgressBar(ctx.author)}\n{objectives.getNextLevelRequirement(getPlayerData(ctx.author, "level", default=1)) - getPlayerData(ctx.author, "xp", default=0)} xp required for next level""",
                                  color=0x00ff00)
            embed.set_thumbnail(url=ctx.author.avatar_url)
            await ctx.send(embed=embed)
            await objectives.addXP(ctx.author, 0)


@client.command()
async def addPermission(ctx, arg=None, perm=None):
    if await permissions.hasPermission(ctx, "admin.permissions.addPermission"):
        if arg is not None and perm is not None:
            member = None
            roleArg = None
            try:
                converter = commands.MemberConverter()
                member = await converter.convert(ctx, arg)
            except commands.MemberNotFound:
                try:
                    converter = commands.RoleConverter()
                    roleArg = await converter.convert(ctx, arg)
                except commands.RoleNotFound:
                    await ctx.send(embed=discord.Embed(
                        title=":x: Couldn't find that member or role! correct usage: !removePermission <member/role> <permission>"))
                except:
                    await ctx.message.channel.send(":x: an unknown error occurred!")
                    raise
            except:
                await ctx.message.channel.send(":x: an unknown error occurred!")
                raise
            if member is not None:
                if permissions.isValidPermission(perm):
                    if not permissions.memberHasPermission(member, perm, bypassRoles=True):
                        permissions.addPermissionToMember(member, perm)
                        await ctx.send(embed=discord.Embed(
                            title=f":white_check_mark: {perm} has been added to {member.display_name}'s permissions",
                            color=0x00ff00))
                    else:
                        await ctx.send(
                            embed=discord.Embed(title=f":x: {member.display_name} already has the permission {perm}.",
                                                description=f"Use !permissions {member.mention} to view their permissions",
                                                color=0xff0000))
                else:
                    await ctx.send(embed=discord.Embed(title=f":x: {perm} is not a valid permission.",
                                                       description="To view a list of all permissions, use !permissions.\n\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help\n(* means all permissions in that permission group)",
                                                       color=0xff0000))
            elif roleArg is not None:
                if permissions.isValidPermission(perm):
                    if not permissions.roleHasPermission(roleArg, perm):
                        permissions.addPermissionToRole(roleArg, perm)
                        await ctx.send(embed=discord.Embed(
                            title=f""":white_check_mark: {perm} has been added to the permissions "{roleArg}" """,
                            color=0x00ff00))
                    else:
                        await ctx.send(
                            embed=discord.Embed(title=f":x: the role {roleArg.name} already has the permission {perm}",
                                                description=f"to view its permissions, do !permissions {roleArg.mention}",
                                                color=0xff0000))
                else:
                    await ctx.send(embed=discord.Embed(title=f":x: {perm} is not a valid permission.",
                                                       description="To view a list of all permissions, use !permissions.\n\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help\n(* means all permissions in that permission group)",
                                                       color=0xff0000))
        else:
            await ctx.send(embed=discord.Embed(title=":x: Incorrect command usage",
                                               description="correct usage: !addPermission <member> <permission>\nuse !permissions to view a list of permissions",
                                               color=0xff0000))


@client.command()
async def removePermission(ctx, arg=None, perm: str = None):
    if await permissions.hasPermission(ctx, "admin.permissions.removePermission"):
        if arg is not None and perm is not None:
            member = None
            roleArg = None
            try:
                converter = commands.MemberConverter()
                member = await converter.convert(ctx, arg)
            except commands.MemberNotFound:
                try:
                    converter = commands.RoleConverter()
                    roleArg = await converter.convert(ctx, arg)
                except commands.RoleNotFound:
                    await ctx.send(embed=discord.Embed(
                        title=":x: Couldn't find that member or role! correct usage: !removePermission <member/role> <permission>"))
                except:
                    await ctx.message.channel.send(":x: an unknown error occurred!")
                    raise
            except:
                await ctx.message.channel.send(":x: an unknown error occurred!")
                raise
            if member is not None:
                if permissions.isValidPermission(perm):
                    if permissions.memberHasPermission(member, perm, bypassRoles=True):
                        permissions.removePermissionFromMember(member, perm)
                        await ctx.send(embed=discord.Embed(
                            title=f":white_check_mark: {perm} has been removed from {member.display_name}'s permissions",
                            color=0x00ff00))
                    else:
                        await ctx.send(
                            embed=discord.Embed(title=f":x: {member.display_name} doesn't have the permission {perm}.",
                                                description=f"Use !permissions {member.mention} to view their permissions",
                                                color=0xff0000))
                else:
                    await ctx.send(embed=discord.Embed(title=f":x: {perm} is not a valid permission.",
                                                       description="To view a list of all permissions, use !permissions.\n\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help\n(* means all permissions in that permission group)",
                                                       color=0xff0000))
            elif roleArg is not None:
                if permissions.isValidPermission(perm):
                    if permissions.roleHasPermission(roleArg, perm):
                        permissions.removePermissionFromRole(roleArg, perm)
                        await ctx.send(embed=discord.Embed(
                            title=f":white_check_mark: The permissions {perm} has been removed from the role {roleArg.name}",
                            color=0x00ff00))
                    else:
                        await ctx.send(
                            embed=discord.Embed(title=f":x: The role {roleArg.name} doesn't have the permission {perm}",
                                                description=f"Use !permissions {roleArg.mention} to view its permissions",
                                                color=0xff0000))
                else:
                    await ctx.send(embed=discord.Embed(title=f":x: {perm} is not a valid permission.",
                                                       description="To view a list of all permissions, use !permissions.\n\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help\n(* means all permissions in that permission group)",
                                                       color=0xff0000))
        else:
            await ctx.send(embed=discord.Embed(title=":x: Incorrect command usage",
                                               description="correct usage: !removePermission <member> <permission>\nuse !permissions to view a list of permissions",
                                               color=0xff0000))


@client.command(aliases=["permissions"])
async def permission(ctx, arg=None):
    if await permissions.hasPermission(ctx, "admin.permissions.permissions"):
        if arg is not None:
            member = None
            roleArg = None
            try:
                converter = commands.MemberConverter()
                member = await converter.convert(ctx, arg)
            except commands.MemberNotFound:
                try:
                    converter = commands.RoleConverter()
                    roleArg = await converter.convert(ctx, arg)
                except commands.RoleNotFound:
                    await ctx.send(embed=discord.Embed(
                        title=":x: Couldn't find that member or role! correct usage: !removePermission <member/role> <permission>"))
                except:
                    await ctx.message.channel.send(":x: an unknown error occurred!")
                    raise
            except:
                await ctx.message.channel.send(":x: an unknown error occurred!")
                raise
            if member is not None:
                await ctx.send(embed=discord.Embed(title=f"Permissions of {member.display_name}",
                                                   description=f"{permissions.getPermissionTree(permissions.getMemberPermissions(member))}\n\nTo add/remove permissions, use !addPermission {member.mention} <permission> or !removePermission {member.mention} <permission>\nUse !permissions to view a full list of permissions\nUse . between permission groups. Use * to select everything in a permission group.\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help",
                                                   color=0x00b8ff))

            elif roleArg is not None:
                await ctx.send(embed=discord.Embed(title=f"""Permissions of "{roleArg.name}" """,
                                                   description=f"{permissions.getPermissionTree(permissions.getRolePermissions(roleArg))}\n\nTo add/remove permissions, use !addPermission {roleArg.mention} <permission> or !removePermission {roleArg.mention} <permission>\nUse !permissions to view a full list of permissions\nUse . between permission groups. Use * to select everything in a permission group.\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help",
                                                   color=0x00b8ff))
        else:
            await ctx.send(embed=discord.Embed(title=f"List of permissions",
                                               description=f"{permissions.getPermissionTree(permissions.getPermissionList())}\n\nTo add/remove permissions, use !addPermission <player/role> <permission> or !removePermission <player/role> <permission>\nUse . between permission groups. Use * to select everything in a permission group.\nExamples of valid permissions:\nadmin.permissions.addPermission\nadmin.*\nadmin.permissions.*\nmember.*\nmember.help",
                                               color=0x00b8ff))


@client.command()
async def invite(ctx):
    await ctx.send(embed=discord.Embed(title="Invite Murder Mystery to your own server!", description="Click this link to invite this bot to your server: https://discord.com/api/oauth2/authorize?client_id=590980247801954304&permissions=2434133072&scope=bot", color=0x00b8ff))
@client.command()
async def error(ctx):
    int("e")

# logging
logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='log.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# get the token from token.txt
tokenFile = open("token.txt", "r")
token = tokenFile.read()
tokenFile.close()
# run token
client.run(token)
