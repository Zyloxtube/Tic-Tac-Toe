import discord
from discord import app_commands
from discord.ui import Button, View
from PIL import Image, ImageDraw, ImageFont
import io
import asyncio
from typing import Optional, Dict
from flask import Flask, jsonify
import threading
import os
import time

# Flask app for health checks
app = Flask(__name__)

# Store active duels
active_duels: Dict[str, 'DuelGame'] = {}

# Track which users are currently in an ACTIVE match (game started)
users_in_match: Dict[int, str] = {}  # user_id -> duel_key

class DuelGame:
    def __init__(self, player1: discord.User, player2: discord.User):
        self.player1 = player1
        self.player2 = player2
        self.board = [""] * 9
        self.current_turn = "X"  # X always starts
        self.winner = None
        self.winning_cells = []
        self.game_over = False
        self.current_message = None
        self.channel = None
        self.started = False
        self.cancelled = False
        
    def make_move(self, position: int) -> bool:
        if self.game_over or self.board[position] != "" or self.cancelled:
            return False
            
        self.board[position] = self.current_turn
        self.check_winner()
        
        if not self.game_over:
            self.current_turn = "O" if self.current_turn == "X" else "X"
        return True
    
    def check_winner(self):
        win_patterns = [
            [0,1,2], [3,4,5], [6,7,8],  # rows
            [0,3,6], [1,4,7], [2,5,8],  # columns
            [0,4,8], [2,4,6]             # diagonals
        ]
        
        for pattern in win_patterns:
            cells = [self.board[i] for i in pattern]
            if cells[0] and cells[0] == cells[1] == cells[2]:
                self.winner = self.player1 if cells[0] == "X" else self.player2
                self.game_over = True
                self.winning_cells = pattern
                return
                
        if all(cell != "" for cell in self.board):
            self.game_over = True
            self.winner = None  # Tie
    
    def draw_board(self) -> bytes:
        CELL_SIZE = 150
        GRID_SIZE = CELL_SIZE * 3
        
        img = Image.new("RGB", (GRID_SIZE, GRID_SIZE), "white")
        draw = ImageDraw.Draw(img)
        
        # Draw grid
        for i in range(1, 3):
            draw.line((i * CELL_SIZE, 0, i * CELL_SIZE, GRID_SIZE), fill="black", width=5)
            draw.line((0, i * CELL_SIZE, GRID_SIZE, i * CELL_SIZE), fill="black", width=5)
        
        # Draw symbols or numbers
        for i, cell in enumerate(self.board):
            x = (i % 3) * CELL_SIZE
            y = (i // 3) * CELL_SIZE
            
            if cell == "X":
                draw.line((x+30, y+30, x+120, y+120), fill="red", width=8)
                draw.line((x+120, y+30, x+30, y+120), fill="red", width=8)
            elif cell == "O":
                draw.ellipse((x+30, y+30, x+120, y+120), outline="blue", width=8)
            else:
                # Draw number in empty cell
                try:
                    font = ImageFont.truetype("arial.ttf", 60)
                except:
                    font = ImageFont.load_default()
                
                number = str(i + 1)
                bbox = draw.textbbox((0, 0), number, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                text_x = x + (CELL_SIZE - text_width) // 2
                text_y = y + (CELL_SIZE - text_height) // 2
                
                draw.text((text_x, text_y), number, fill="gray", font=font)
        
        # Draw winning line if exists
        if self.winning_cells:
            start_x = (self.winning_cells[0] % 3) * CELL_SIZE + CELL_SIZE // 2
            start_y = (self.winning_cells[0] // 3) * CELL_SIZE + CELL_SIZE // 2
            end_x = (self.winning_cells[2] % 3) * CELL_SIZE + CELL_SIZE // 2
            end_y = (self.winning_cells[2] // 3) * CELL_SIZE + CELL_SIZE // 2
            draw.line((start_x, start_y, end_x, end_y), fill="green", width=10)
        
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return img_buffer

class TicTacToeView(View):
    def __init__(self, game: DuelGame, current_player: discord.User):
        super().__init__(timeout=300)
        self.game = game
        self.current_player = current_player
        self.add_buttons()
    
    def add_buttons(self):
        # Row 0 (top row)
        self.add_item(self.create_button(0, 0))
        self.add_item(self.create_button(1, 0))
        self.add_item(self.create_button(2, 0))
        
        # Row 1 (middle row)
        self.add_item(self.create_button(3, 1))
        self.add_item(self.create_button(4, 1))
        self.add_item(self.create_button(5, 1))
        
        # Row 2 (bottom row)
        self.add_item(self.create_button(6, 2))
        self.add_item(self.create_button(7, 2))
        self.add_item(self.create_button(8, 2))
    
    def create_button(self, position: int, row: int) -> Button:
        is_empty = self.game.board[position] == ""
        
        if is_empty:
            label = str(position + 1)
            style = discord.ButtonStyle.primary
            disabled = False
        else:
            label = self.game.board[position]
            style = discord.ButtonStyle.secondary
            disabled = True
        
        button = Button(label=label, style=style, disabled=disabled, row=row)
        button.callback = self.create_callback(position)
        return button
    
    def create_callback(self, position: int):
        async def callback(interaction: discord.Interaction):
            if self.game.cancelled:
                await interaction.response.send_message("This match has been cancelled!", ephemeral=True)
                return
                
            expected_player = self.game.player1 if self.game.current_turn == "X" else self.game.player2
            if interaction.user != expected_player:
                await interaction.response.send_message("It's not your turn!", ephemeral=True)
                return
            
            if self.game.game_over or self.game.board[position] != "":
                await interaction.response.send_message("That spot is already taken!", ephemeral=True)
                return
            
            # Make the move
            self.game.make_move(position)
            
            # Draw new board
            board_img = self.game.draw_board()
            file = discord.File(board_img, filename="board.png")
            
            # Create new view for next turn
            new_view = TicTacToeView(self.game, 
                                     self.game.player2 if self.game.current_turn == "X" else self.game.player1)
            
            # Check game status
            if self.game.game_over:
                if self.game.winner:
                    result_msg = f"🏆 **{self.game.winner.display_name}** won the game! 🏆\n{self.game.player1.display_name if self.game.winner == self.game.player2 else self.game.player2.display_name} lost!"
                else:
                    result_msg = "It's a tie! 🤝"
                
                # Edit the message to show result and remove buttons
                await interaction.response.edit_message(content=result_msg, view=None, attachments=[file])
                
                # Clean up
                duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
                reverse_key = f"{self.game.player2.id}_{self.game.player1.id}"
                active_duels.pop(duel_key, None)
                active_duels.pop(reverse_key, None)
                users_in_match.pop(self.game.player1.id, None)
                users_in_match.pop(self.game.player2.id, None)
            else:
                turn_msg = f"🎮 **{self.game.current_turn}**'s turn ({expected_player.display_name})\nClick the buttons below to play!"
                
                # Edit the existing message with new board and buttons
                await interaction.response.edit_message(content=turn_msg, view=new_view, attachments=[file])
        
        return callback

class DuelView(View):
    def __init__(self, game: DuelGame, challenger: discord.User, challenged: discord.User):
        super().__init__(timeout=60)
        self.game = game
        self.challenger = challenger
        self.challenged = challenged
    
    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green, row=0)
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("❌ This duel isn't for you!", ephemeral=True)
            return
        
        if self.game.cancelled:
            await interaction.response.send_message("This duel has been cancelled!", ephemeral=True)
            return
        
        self.game.started = True
        
        # Mark both users as being in a match
        users_in_match[self.game.player1.id] = f"{self.game.player1.id}_{self.game.player2.id}"
        users_in_match[self.game.player2.id] = f"{self.game.player1.id}_{self.game.player2.id}"
        
        # Draw initial board
        board_img = self.game.draw_board()
        file = discord.File(board_img, filename="board.png")
        
        # Create game view for first player
        first_player = self.game.player1
        game_view = TicTacToeView(self.game, first_player)
        
        turn_msg = f"⚔️ **Match has begun between {self.game.player1.display_name} and {self.game.player2.display_name}** ⚔️\n**{self.game.player1.display_name} is X and {self.game.player2.display_name} is O**\n\n🎮 **{self.game.current_turn}**'s turn ({first_player.display_name})\nClick the buttons below to play!"
        
        # Edit the challenge message to start the game
        await interaction.response.edit_message(content=turn_msg, view=game_view, attachments=[file])
        
        # Remove challenge from active_duels
        duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
        reverse_key = f"{self.game.player2.id}_{self.game.player1.id}"
        active_duels.pop(duel_key, None)
        active_duels.pop(reverse_key, None)
    
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red, row=0)
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("❌ This duel isn't for you!", ephemeral=True)
            return
        
        if self.game.cancelled:
            await interaction.response.send_message("This duel has been cancelled!", ephemeral=True)
            return
        
        # Edit the message to show refusal and remove buttons
        await interaction.response.edit_message(content=f"😔 {self.challenged.display_name} refused to duel!", view=None)
        
        # Clean up
        duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
        reverse_key = f"{self.game.player2.id}_{self.game.player1.id}"
        active_duels.pop(duel_key, None)
        active_duels.pop(reverse_key, None)

class CancelView(View):
    def __init__(self, game: DuelGame, canceller: discord.User, opponent: discord.User):
        super().__init__(timeout=30)
        self.game = game
        self.canceller = canceller
        self.opponent = opponent
    
    @discord.ui.button(label="✅ Confirm Cancel", style=discord.ButtonStyle.red, row=0)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ This cancel request isn't for you!", ephemeral=True)
            return
        
        self.game.cancelled = True
        
        # Edit the message to show cancellation
        await interaction.response.edit_message(content=f"❌ **{self.canceller.display_name}** cancelled the match!", view=None)
        
        # Clean up
        duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
        reverse_key = f"{self.game.player2.id}_{self.game.player1.id}"
        active_duels.pop(duel_key, None)
        active_duels.pop(reverse_key, None)
        users_in_match.pop(self.game.player1.id, None)
        users_in_match.pop(self.game.player2.id, None)
    
    @discord.ui.button(label="❌ Keep Match", style=discord.ButtonStyle.green, row=0)
    async def keep_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ This cancel request isn't for you!", ephemeral=True)
            return
        
        # Edit the message to show match continues
        await interaction.response.edit_message(content=f"✅ {self.opponent.display_name} wants to continue the match!", view=None)

class DuelBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"Synced commands for {self.user}")

bot = DuelBot()

@bot.tree.command(name="duel", description="Challenge another user to a Tic-Tac-Toe duel!")
async def duel(interaction: discord.Interaction, opponent: discord.User):
    if opponent == interaction.user:
        await interaction.response.send_message("You can't duel yourself!", ephemeral=True)
        return
    
    if interaction.user.id in users_in_match:
        await interaction.response.send_message("❌ You can't duel while you're in a match! Use /cancel first.", ephemeral=True)
        return
    
    if opponent.id in users_in_match:
        await interaction.response.send_message(f"❌ {opponent.display_name} is already in a match!", ephemeral=True)
        return
    
    duel_key = f"{interaction.user.id}_{opponent.id}"
    reverse_key = f"{opponent.id}_{interaction.user.id}"
    
    if duel_key in active_duels or reverse_key in active_duels:
        await interaction.response.send_message("A duel challenge already exists! Wait for them to respond.", ephemeral=True)
        return
    
    game = DuelGame(interaction.user, opponent)
    active_duels[duel_key] = game
    
    view = DuelView(game, interaction.user, opponent)
    
    await interaction.response.send_message(
        f"🎯 **{interaction.user.display_name}** challenged **{opponent.display_name}** to Tic-Tac-Toe!\n"
        f"{opponent.mention}, do you accept?",
        view=view
    )

@bot.tree.command(name="cancel", description="Cancel your current duel")
async def cancel(interaction: discord.Interaction):
    # Check for active match
    if interaction.user.id in users_in_match:
        for key, game in active_duels.items():
            if (game.player1 == interaction.user or game.player2 == interaction.user) and game.started:
                opponent = game.player2 if game.player1 == interaction.user else game.player1
                view = CancelView(game, interaction.user, opponent)
                await interaction.response.send_message(
                    f"⚠️ **{interaction.user.display_name}** wants to cancel!\n"
                    f"{opponent.mention}, agree?",
                    view=view
                )
                return
    
    # Check for pending challenge
    for key, game in active_duels.items():
        if (game.player1 == interaction.user or game.player2 == interaction.user) and not game.started:
            opponent = game.player2 if game.player1 == interaction.user else game.player1
            game.cancelled = True
            await interaction.response.send_message(f"❌ **{interaction.user.display_name}** cancelled the challenge!")
            reverse_key = f"{game.player2.id}_{game.player1.id}"
            active_duels.pop(key, None)
            active_duels.pop(reverse_key, None)
            return
    
    await interaction.response.send_message("❌ You're not in any duel or match!", ephemeral=True)

@app.route('/')
def home():
    return jsonify({"status": "alive", "bot": "Tic-Tac-Toe Duel Bot"})

@app.route('/ping')
def ping():
    return jsonify({"status": "pong", "message": "Bot is running!"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "active_duels": len(active_duels), "users_in_match": len(users_in_match)})

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    token = os.environ.get('DISCORD_BOT_TOKEN')
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set!")
        return
    bot.run(token)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
