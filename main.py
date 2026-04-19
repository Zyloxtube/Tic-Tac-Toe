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
import chess
import chess.svg
import cairosvg

# Flask app for health checks
app = Flask(__name__)

# Store active duels
active_duels: Dict[str, 'DuelGame'] = {}

# Track which users are currently in an ACTIVE match (game started)
users_in_match: Dict[int, str] = {}  # user_id -> duel_key

class ChessGame:
    def __init__(self, player1: discord.User, player2: discord.User):
        self.player1 = player1  # White
        self.player2 = player2  # Black
        self.board = chess.Board()
        self.current_turn = "White"  # White always starts
        self.winner = None
        self.game_over = False
        self.current_message = None
        self.channel = None
        self.started = False
        self.cancelled = False
        self.waiting_for_move = False
        self.current_player_id = player1.id
        
    async def make_move(self, move_san: str, user_id: int, interaction: discord.Interaction) -> tuple[bool, str]:
        """Make a move. Returns (success, message)"""
        if self.game_over or self.cancelled:
            return False, "Game is over!"
        
        # Check if it's the player's turn
        expected_player = self.player1.id if self.current_turn == "White" else self.player2.id
        if user_id != expected_player:
            return False, "Not your turn!"
        
        try:
            # Try to parse the move
            move = self.board.parse_san(move_san)
            if move in self.board.legal_moves:
                self.board.push(move)
                
                # Check game over conditions
                if self.board.is_checkmate():
                    self.game_over = True
                    self.winner = self.player1 if self.current_turn == "White" else self.player2
                    return True, f"Checkmate! {self.winner.display_name} wins!"
                elif self.board.is_stalemate():
                    self.game_over = True
                    return True, "Stalemate! Game is a draw."
                elif self.board.is_insufficient_material():
                    self.game_over = True
                    return True, "Insufficient material! Game is a draw."
                
                # Switch turns
                self.current_turn = "Black" if self.current_turn == "White" else "White"
                self.current_player_id = self.player2.id if self.current_turn == "White" else self.player1.id
                
                # Check for check
                if self.board.is_check():
                    current_player_name = self.player1.display_name if self.current_turn == "White" else self.player2.display_name
                    return True, f"Move accepted! Check! {current_player_name}'s turn."
                else:
                    current_player_name = self.player1.display_name if self.current_turn == "White" else self.player2.display_name
                    return True, f"Move accepted! {current_player_name}'s turn."
            else:
                return False, "Illegal move!"
        except Exception as e:
            return False, f"Invalid move format! Use algebraic notation (e.g., 'e4', 'Nf3', 'O-O')"
    
    async def get_board_image(self) -> discord.File:
        """Generate PNG image of current board (async wrapper)"""
        # Run SVG generation in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        svg_string = await loop.run_in_executor(None, lambda: chess.svg.board(board=self.board, size=400))
        png_bytes = await loop.run_in_executor(None, lambda: cairosvg.svg2png(bytestring=svg_string.encode('utf-8')))
        return discord.File(io.BytesIO(png_bytes), filename='chess.png')
    
    def get_legal_moves_text(self) -> str:
        """Get list of legal moves as string"""
        moves = list(self.board.legal_moves)
        san_moves = [self.board.san(move) for move in moves[:15]]  # Limit to 15
        moves_text = ", ".join(san_moves)
        if len(moves) > 15:
            moves_text += f"... and {len(moves)-15} more"
        return moves_text

class TicTacToeGame:
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

class ChessView(View):
    def __init__(self, game: ChessGame):
        super().__init__(timeout=300)
        self.game = game
        
    @discord.ui.button(label="♟️ Make Move", style=discord.ButtonStyle.primary, row=0)
    async def move_button(self, interaction: discord.Interaction, button: Button):
        if self.game.cancelled or self.game.game_over:
            await interaction.response.send_message("Game is over!", ephemeral=True)
            return
        
        # Send modal for move input (better than waiting for message)
        modal = MoveModal(self.game)
        await interaction.response.send_modal(modal)

class MoveModal(discord.ui.Modal):
    def __init__(self, game: ChessGame):
        super().__init__(title="Make Your Chess Move")
        self.game = game
        
        self.move_input = discord.ui.TextInput(
            label="Enter your move",
            placeholder="Example: e4, Nf3, O-O, exd5",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.move_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        # Defer to avoid timeout
        await interaction.response.defer()
        
        move = self.move_input.value.strip()
        success, result = await self.game.make_move(move, interaction.user.id, interaction)
        
        if success:
            # Get board image (this is slow, so do it after defer)
            board_file = await self.game.get_board_image()
            
            if self.game.game_over:
                await interaction.edit_original_response(
                    content=f"🏆 **{result}** 🏆",
                    attachments=[board_file],
                    view=None
                )
                # Clean up
                duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
                active_duels.pop(duel_key, None)
                users_in_match.pop(self.game.player1.id, None)
                users_in_match.pop(self.game.player2.id, None)
            else:
                current_player = self.game.player1 if self.game.current_turn == "White" else self.game.player2
                
                # Update the original game message
                embed = discord.Embed(
                    title="♜ Chess Game",
                    description=f"{result}\n\n**{current_player.mention}'s turn**\n\n📝 Use the button below to make your move!",
                    color=discord.Color.blue()
                )
                
                await interaction.edit_original_response(
                    content=None,
                    embed=embed,
                    attachments=[board_file],
                    view=ChessView(self.game)
                )
        else:
            await interaction.followup.send(f"❌ {result}", ephemeral=True)

class TicTacToeView(View):
    def __init__(self, game: TicTacToeGame, current_player: discord.User):
        super().__init__(timeout=300)
        self.game = game
        self.current_player = current_player
        self.add_buttons()
    
    def add_buttons(self):
        for i in range(9):
            button = self.create_button(i, i // 3)
            self.add_item(button)
    
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
        
        async def callback(interaction: discord.Interaction):
            if self.game.cancelled:
                await interaction.response.send_message("Match cancelled!", ephemeral=True)
                return
                
            expected_player = self.game.player1 if self.game.current_turn == "X" else self.game.player2
            if interaction.user != expected_player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            
            if self.game.game_over or self.game.board[position] != "":
                await interaction.response.send_message("Spot taken!", ephemeral=True)
                return
            
            self.game.make_move(position)
            board_img = self.game.draw_board()
            file = discord.File(board_img, filename="board.png")
            
            new_view = TicTacToeView(self.game, self.game.player2 if self.game.current_turn == "X" else self.game.player1)
            
            if self.game.game_over:
                if self.game.winner:
                    result_msg = f"🏆 **{self.game.winner.display_name}** won!"
                else:
                    result_msg = "It's a tie! 🤝"
                
                await interaction.response.edit_message(content=result_msg, view=None, attachments=[file])
                
                duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
                active_duels.pop(duel_key, None)
                users_in_match.pop(self.game.player1.id, None)
                users_in_match.pop(self.game.player2.id, None)
            else:
                turn_msg = f"🎮 **Tic Tac Toe**\n{self.game.current_turn}'s turn ({expected_player.display_name})"
                await interaction.response.edit_message(content=turn_msg, view=new_view, attachments=[file])
        
        button.callback = callback
        return button

class GameSelectView(View):
    def __init__(self, challenger: discord.User, challenged: discord.User):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
    
    @discord.ui.button(label="❌ Tic Tac Toe", style=discord.ButtonStyle.primary, emoji="❌", row=0)
    async def tictactoe_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Not for you!", ephemeral=True)
            return
        await self.start_game(interaction, "tictactoe")
    
    @discord.ui.button(label="♜ Chess", style=discord.ButtonStyle.success, emoji="♜", row=0)
    async def chess_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Not for you!", ephemeral=True)
            return
        await self.start_game(interaction, "chess")
    
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger, row=1)
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Not for you!", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"😔 {self.challenged.display_name} refused!", view=None)
    
    async def start_game(self, interaction: discord.Interaction, game_type: str):
        await interaction.response.defer()
        
        users_in_match[self.challenger.id] = f"{self.challenger.id}_{self.challenged.id}"
        users_in_match[self.challenged.id] = f"{self.challenger.id}_{self.challenged.id}"
        
        if game_type == "tictactoe":
            game = TicTacToeGame(self.challenger, self.challenged)
            active_duels[f"{self.challenger.id}_{self.challenged.id}"] = game
            
            board_img = game.draw_board()
            file = discord.File(board_img, filename="board.png")
            game_view = TicTacToeView(game, self.challenger)
            
            await interaction.edit_original_response(
                content=f"⚔️ **{self.challenger.display_name} vs {self.challenged.display_name}**\n**X's turn ({self.challenger.display_name})**",
                view=game_view,
                attachments=[file]
            )
            
        else:  # chess
            game = ChessGame(self.challenger, self.challenged)
            active_duels[f"{self.challenger.id}_{self.challenged.id}"] = game
            game.channel = interaction.channel
            
            board_file = await game.get_board_image()
            chess_view = ChessView(game)
            
            embed = discord.Embed(
                title="♜ Chess Match",
                description=f"**{self.challenger.display_name} (White) vs {self.challenged.display_name} (Black)**\n\n**White's turn ({self.challenger.display_name})**\n\n📝 Click the button below to make your move!",
                color=discord.Color.gold()
            )
            
            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=chess_view,
                attachments=[board_file]
            )

class CancelView(View):
    def __init__(self, game, canceller: discord.User, opponent: discord.User):
        super().__init__(timeout=30)
        self.game = game
        self.canceller = canceller
        self.opponent = opponent
    
    @discord.ui.button(label="✅ Confirm Cancel", style=discord.ButtonStyle.red, row=0)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("Not for you!", ephemeral=True)
            return
        
        self.game.cancelled = True
        await interaction.response.edit_message(content=f"❌ **{self.canceller.display_name}** cancelled!", view=None)
        
        duel_key = f"{self.game.player1.id}_{self.game.player2.id}"
        active_duels.pop(duel_key, None)
        users_in_match.pop(self.game.player1.id, None)
        users_in_match.pop(self.game.player2.id, None)
    
    @discord.ui.button(label="❌ Keep Match", style=discord.ButtonStyle.green, row=0)
    async def keep_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("Not for you!", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"✅ Match continues!", view=None)

class DuelBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Synced commands for {self.user}")

bot = DuelBot()

@bot.tree.command(name="duel", description="Challenge someone to a game!")
async def duel(interaction: discord.Interaction, opponent: discord.User):
    if opponent == interaction.user:
        await interaction.response.send_message("Can't duel yourself!", ephemeral=True)
        return
    
    if interaction.user.id in users_in_match:
        await interaction.response.send_message("❌ You're already in a match! Use /cancel first.", ephemeral=True)
        return
    
    if opponent.id in users_in_match:
        await interaction.response.send_message(f"❌ {opponent.display_name} is already in a match!", ephemeral=True)
        return
    
    view = GameSelectView(interaction.user, opponent)
    await interaction.response.send_message(
        f"🎯 **{interaction.user.display_name}** challenged **{opponent.display_name}**!\n{opponent.mention}, choose your game:",
        view=view
    )

@bot.tree.command(name="cancel", description="Cancel your current match")
async def cancel(interaction: discord.Interaction):
    if interaction.user.id in users_in_match:
        duel_key = users_in_match[interaction.user.id]
        game = active_duels.get(duel_key)
        
        if game:
            opponent = game.player2 if game.player1 == interaction.user else game.player1
            view = CancelView(game, interaction.user, opponent)
            await interaction.response.send_message(
                f"⚠️ **{interaction.user.display_name}** wants to cancel!\n{opponent.mention}, agree?",
                view=view
            )
            return
    
    await interaction.response.send_message("❌ You're not in any match!", ephemeral=True)

@app.route('/')
def home():
    return jsonify({"status": "alive", "bot": "Multi-Game Duel Bot"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "active_duels": len(active_duels)})

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    token = os.environ.get('DISCORD_BOT_TOKEN')
    if not token:
        print("Error: DISCORD_BOT_TOKEN not set!")
        return
    bot.run(token)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
