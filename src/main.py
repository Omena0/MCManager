from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import tkinter.filedialog as filedialog
from validators import Validators
import matplotlib.pyplot as plt
import customtkinter as tki
from servers import Server
import dns.resolver
import subprocess
import threading
import traceback
import requests
import socket
import psutil
import time
import json
import sys
import os
import re

tki.set_appearance_mode('dark')

def _resolve_hostname(hostname):
    """More robust hostname resolution using multiple DNS servers"""

    # Try system's default DNS first
    try:
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except socket.gaierror:...

    # Try multiple public DNS resolvers if system DNS fails
    dns_servers = [
        '1.1.1.1',       # Cloudflare
        '8.8.8.8',       # Google
        '9.9.9.9',       # Quad9
        '208.67.222.222' # OpenDNS
    ]

    for dns_server in dns_servers:
        try:
            # Create a resolver using the specific DNS server
            resolver = dns.resolver.Resolver()
            resolver.nameservers = [dns_server]

            # Query A record
            answers = resolver.query(hostname, 'A')
            for rdata in answers:
                ip_address = str(rdata)
                return ip_address
        except Exception: ...

    # If all DNS resolvers fail, try a HTTP-based DNS lookup
    try:
        import requests
        response = requests.get(f"https://dns.google/resolve?name={hostname}")
        if response.status_code == 200:
            data = response.json()
            if "Answer" in data:
                for answer in data["Answer"]:
                    if answer["type"] == 1:  # Type A record
                        ip_address = answer["data"]
                        return ip_address
    except Exception: ...

    # If all attempts fail
    return None

class MCManager(tki.CTk):
    def __init__(self, args:list[str] | None = None):
        # sourcery skip: low-code-quality
        self.tk_running = False

        if args is None:
            args = []

        if '-nogui' not in args:
           super().__init__()

        self.args = args
        self.servers = self.get_server_list()


        if '-install' in args:
            argCount = 0
            install_index = args.index('-install')
            i = install_index + 1
            while i < len(args) and not args[i].startswith('-'):
                argCount += 1
                i += 1

            name = args[args.index('-install')+1] if argCount >= 1 else 'server'
            software = args[args.index('-install')+2] if argCount >= 2 else 'purpur'
            version = args[args.index('-install')+3] if argCount >= 3 else '1.21.4'
            port = args[args.index('-install')+4] if argCount >= 4 else '25565'
            memory = int(args[args.index('-install')+5]) if argCount >= 5 else 4096
            backup_str = args[args.index('-install')+6] if argCount >= 6 else None

            print(f'Creating server: {name}. Version: {software} {version}')
            self._create_server_thread(name, software, version, port, memory, backup_str)

        if '-nogui' in args:
            if '-autostart' in args:
                self.servers = self.get_server_list()
                server_id = args[args.index('-autostart')+1]

                if server_id in self.servers:
                    self.current_server = Server(server_id)
                    self.start_server()
                else:
                    exit(f'Error: No such server: {server_id}')
            self.running = True
            return

        self.running = False

        # Configure window
        self.title("Minecraft Server Manager")
        self.geometry("1000x600")
        self.minsize(700, 500)

        # Protocol handler for window close
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Check if servers exist
        if not self.servers:
            # No servers found, show empty state UI
            self.show_no_servers_ui()
        else:
            # Servers exist, initialize normal UI
            self.initialize_main_ui()

        # Start monitoring thread if main UI is initialized
        if hasattr(self, 'current_server'):
            self.running = True
            self.monitor_thread = threading.Thread(target=self.monitor_resources)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            if '-autostart' in args:
                server_id = args[args.index('-autostart')+1]

                if server_id in self.servers:
                    self.change_server(server_id)
                    self.start_server()
                else:
                    exit(f'Error: No such server: {server_id}')

    def run(self):
        if '-nogui' not in self.args:
            self.mainloop()
        else:
            self.monitor_thread = threading.Thread(target=self.monitor_resources,daemon=True)
            self.monitor_thread.start()

            while self.running:
                command = input('')
                self.current_server.send_command(command)

    def mainloop(self, *args, **kwargs):
        self.tk_running = True
        try:
            return super().mainloop(*args, **kwargs)
        finally:
            self.tk_running = False

    def on_closing(self):
        """Handle application shutdown properly - with FORCED silent exit"""
        # Set running flag to False to stop monitoring thread
        self.running = False

        try:
            self.current_server.stop()
        except Exception:
            ...
        try:
            self.ngrok_process.terminate()
        except Exception:
            ...

        # First redirect both stdout and stderr to null device
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

        # Cancel any pending after callbacks (this is a more aggressive approach)
        try:
            for after_id in self.tk.call('after', 'info'):
                self.tk.call('after', 'cancel', after_id)
        except Exception:
            pass

        # Force destroy all widgets to avoid callbacks
        for widget in self.winfo_children():
            try:
                widget.destroy()
            except:
                pass

        # Destroy the window
        try:
            self.destroy()
        except:
            pass

        # Use os._exit for immediate termination without cleanup
        os._exit(0)  # This is more forceful than sys.exit()
    def initialize_main_ui(self):
        """Initialize the main application UI with server management"""
        # Configure grid - Assign weights to columns with much greater difference
        self.grid_columnconfigure(0, weight=1)      # Very small weight for sidebar
        self.grid_columnconfigure(1, weight=5)     # Much larger weight for content
        self.grid_rowconfigure(0, weight=1)

        # Create sidebar frame that will resize with the window
        self.sidebar_frame = tki.CTkFrame(
            self,
            corner_radius=5,
            width=180
        )
        self.sidebar_frame.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=10, pady=(18, 10))
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        # Configure sidebar frame's column to make inner widgets responsive
        self.sidebar_frame.grid_columnconfigure(0, weight=1)

        # Prevent the sidebar from collapsing below minimum width
        self.sidebar_frame.grid_propagate(False)

        # App label - SMALLER FONT
        self.app_name = tki.CTkLabel(
            self.sidebar_frame,
            text="MC Manager",
            font=tki.CTkFont(size=16, weight="bold")
        )
        self.app_name.grid(row=0, column=0, pady=(10, 5), sticky="ew")  # Make label expand horizontally

        # Server selection label
        self.server_label = tki.CTkLabel(
            self.sidebar_frame,
            text="Select Server:",
            anchor="w"
        )
        self.server_label.grid(row=1, column=0, padx=10, pady=(5, 0), sticky="ew")  # Make label expand horizontally

        # Server selection dropdown - EXPANDED TO FILL WIDTH
        self.server_option = tki.CTkOptionMenu(
            self.sidebar_frame,
            values=self.servers,
            command=self.change_server,
            dynamic_resizing=False  # Prevent text from being cut off
        )
        self.server_option.grid(row=2, column=0, padx=10, pady=(5, 5), sticky="ew")  # Make dropdown expand horizontally

        # Add new server button
        self.add_server_btn = tki.CTkButton(
            self.sidebar_frame,
            text="Add New Server",
            command=self.start_server_wizard
        )
        self.add_server_btn.grid(row=3, column=0, padx=10, pady=(0, 5), sticky="ew")  # Already set to expand

        # Server controls frame - MAKE RESPONSIVE
        self.control_frame = tki.CTkFrame(self.sidebar_frame)
        self.control_frame.grid(row=4, column=0, padx=10, pady=(5, 5), sticky="new")  # Make frame expand horizontally

        # Configure columns in control frame to distribute space evenly
        self.control_frame.grid_columnconfigure(0, weight=1)
        self.control_frame.grid_columnconfigure(1, weight=1)

        # Control buttons
        self.start_button = tki.CTkButton(
            self.control_frame,
            text="Start",
            height=28,
            command=self.start_server,
            fg_color="green"
        )
        self.start_button.grid(row=0, column=0, padx=3, pady=3, sticky="ew")

        self.stop_button = tki.CTkButton(
            self.control_frame,
            text="Stop",
            height=28,
            command=self.stop_server,
            fg_color="red"
        )
        self.stop_button.grid(row=0, column=1, padx=3, pady=3, sticky="ew")

        self.restart_button = tki.CTkButton(
            self.control_frame,
            text="Restart",
            height=28,
            command=self.restart_server,
            fg_color="orange"
        )
        self.restart_button.grid(row=1, column=0, columnspan=2, padx=3, pady=3, sticky="ew")

        # Add in the control_frame section in initialize_main_ui after the restart_button
        self.tunnel_button = tki.CTkButton(
            self.control_frame,
            text="Start tunnel",
            height=28,
            command=self.start_tunnel,
            fg_color="purple"
        )
        self.tunnel_button.grid(row=2, column=0, columnspan=2, padx=3, pady=3, sticky="ew")

        # Create main content area with tabview - REDUCED PADDING
        self.tabview = tki.CTkTabview(self, corner_radius=6)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=(0,10), pady=(0,10))  # REDUCED PADDING

        # Create tabs
        self.dashboard_tab = self.tabview.add("Dashboard")
        self.console_tab = self.tabview.add("Console")
        self.plugins_tab = self.tabview.add("Plugins")
        self.players_tab = self.tabview.add("Players")
        self.backups_tab = self.tabview.add("Backups")
        self.settings_tab = self.tabview.add("Settings")
        self.optimizations_tab = self.tabview.add("Optimizations")

        # Configure tab grids
        for tab in [self.dashboard_tab, self.console_tab, self.plugins_tab,
                    self.players_tab, self.backups_tab, self.settings_tab,
                    self.optimizations_tab]:
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)

        # Setup tab contents
        self.setup_dashboard_tab()
        self.setup_console_tab()
        self.setup_plugins_tab()
        self.setup_players_tab()
        self.setup_backups_tab()
        self.setup_settings_tab()
        self.setup_optimizations_tab()

        # Initialize with first server if available
        if self.servers:
            self.change_server(self.servers[0])
            self.server_option.set(self.servers[0])
        else:
            self.current_server: Server | None = None

    def show_no_servers_ui(self):
        """Display UI for when no servers are configured - MORE COMPACT"""
        # Configure grid for empty state
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Create centered frame with welcome message and setup button
        self.welcome_frame = tki.CTkFrame(self, corner_radius=10)
        self.welcome_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")  # REDUCED PADDING

        # Make the welcome frame centered rather than filling the window
        self.grid_propagate(False)
        self.welcome_frame.grid_propagate(False)

        # Configure welcome frame grid
        self.welcome_frame.grid_columnconfigure(0, weight=1)

        # App logo/icon - SMALLER TEXT
        self.logo_label = tki.CTkLabel(self.welcome_frame, text="MC Manager",
                                     font=tki.CTkFont(size=28, weight="bold"))  # REDUCED SIZE
        self.logo_label.grid(row=0, column=0, padx=40, pady=(30, 15))  # REDUCED PADDING

        # Welcome message - REDUCED WRAPLENGTH
        welcome_text = "Welcome to Minecraft Server Manager!\n\nNo servers have been configured yet."
        self.welcome_msg = tki.CTkLabel(self.welcome_frame, text=welcome_text,
                                      font=tki.CTkFont(size=14), wraplength=350)  # SMALLER FONT, REDUCED WRAPLENGTH
        self.welcome_msg.grid(row=1, column=0, padx=40, pady=(0, 20))  # REDUCED PADDING

        # Create server button - REDUCED HEIGHT
        self.create_server_btn = tki.CTkButton(self.welcome_frame, text="Create Your First Server",
                                             command=self.start_server_wizard,
                                             height=36, font=tki.CTkFont(size=14, weight="bold"))  # SMALLER HEIGHT, FONT
        self.create_server_btn.grid(row=2, column=0, padx=40, pady=(0, 15))  # REDUCED PADDING

        # Import existing server button - REDUCED HEIGHT
        self.import_server_btn = tki.CTkButton(self.welcome_frame, text="Import Existing Server",
                                             command=self.import_existing_server,
                                             height=36)  # SMALLER HEIGHT
        self.import_server_btn.grid(row=3, column=0, padx=40, pady=(0, 30))  # REDUCED PADDING

    # Wizard
    def start_server_wizard(self):
        """Launch the server creation wizard"""
        # Clear existing UI if needed
        for widget in self.winfo_children():
            widget.destroy()

        # Initialize wizard data storage
        self.saved_wizard_data = {}

        # Create wizard container frame
        self.wizard_frame = tki.CTkFrame(self)
        self.wizard_frame.pack(fill=tki.BOTH, expand=True, padx=20, pady=20)

        # Header frame
        self.wizard_header = tki.CTkFrame(self.wizard_frame)
        self.wizard_header.pack(fill=tki.X, pady=(0, 20))

        # Wizard title
        self.wizard_title = tki.CTkLabel(self.wizard_header, text="Create New Server",
                                       font=tki.CTkFont(size=24, weight="bold"))
        self.wizard_title.pack(side=tki.LEFT, padx=20, pady=20)

        # Steps indicator
        self.wizard_steps = ["Basic Info", "Server Software", "Performance", "Backups & Security", "Summary"]
        self.current_step = 0

        # Progress indicator
        self.wizard_progress = tki.CTkProgressBar(self.wizard_header, width=400)
        self.wizard_progress.pack(side=tki.RIGHT, padx=20, pady=20)
        self.wizard_progress.set(0)

        # Content area - make it scrollable to handle overflow
        self.wizard_content_container = tki.CTkScrollableFrame(self.wizard_frame)
        self.wizard_content_container.pack(fill=tki.BOTH, expand=True, padx=0, pady=0)

        # Actual content frame within the scrollable container
        self.wizard_content = tki.CTkFrame(self.wizard_content_container, fg_color="transparent")
        self.wizard_content.pack(fill=tki.BOTH, expand=True)

        # Navigation buttons
        self.wizard_nav = tki.CTkFrame(self.wizard_frame)
        self.wizard_nav.pack(fill=tki.X, pady=(20, 0))

        self.prev_btn = tki.CTkButton(self.wizard_nav, text="Previous",
                                    command=self.wizard_previous_step)
        self.prev_btn.pack(side=tki.LEFT, padx=20, pady=10)

        self.next_btn = tki.CTkButton(self.wizard_nav, text="Next",
                                    command=self.wizard_next_step)
        self.next_btn.pack(side=tki.RIGHT, padx=20, pady=10)

        # Initialize the first step of the wizard
        self.wizard_show_step(0)

    def wizard_show_step(self, step_index):
        """Display a specific step in the server creation wizard"""
        # Save current step data if needed
        if hasattr(self, 'current_step'):
            self._save_current_step_data()

        self.current_step = step_index

        # Clear current content
        for widget in self.wizard_content.winfo_children():
            widget.destroy()

        # Update progress bar
        progress_value = step_index / (len(self.wizard_steps) - 1)
        self.wizard_progress.set(progress_value)

        # Update nav buttons
        self.prev_btn.configure(state="normal" if step_index > 0 else "disabled")

        if step_index == len(self.wizard_steps) - 1:
            self.next_btn.configure(text="Create Server", command=self.create_server)
        else:
            self.next_btn.configure(text="Next", command=self.wizard_next_step)

        # Show appropriate step content
        if step_index == 0:
            self.wizard_step_basic_info()
        elif step_index == 1:
            self.wizard_step_software()
        elif step_index == 2:
            self.wizard_step_performance()
        elif step_index == 3:
            self.wizard_step_backups()
        elif step_index == 4:
            self.wizard_step_summary()

        # Disable next button if on EULA step and EULA not accepted
        if step_index == 1 and hasattr(self, 'eula_var') and not self.eula_var.get():
            self.next_btn.configure(state="disabled")

    def _save_current_step_data(self):
        """Save the current step's data to ensure it's not lost when navigating"""
        # Don't save if no current step or saved_wizard_data doesn't exist
        if not hasattr(self, 'current_step'):
            return

        # Initialize saved_wizard_data if it doesn't exist
        if not hasattr(self, 'saved_wizard_data'):
            self.saved_wizard_data = {}

        # Step 0 (basic info): Save server name, description, ID, and port
        if self.current_step == 0 and hasattr(self, 'server_name_var'):
            self.saved_wizard_data['server_name'] = self.server_name_var.get()
            self.saved_wizard_data['server_desc'] = self.server_desc_var.get()
            self.saved_wizard_data['server_id'] = self.server_id_var.get()
            self.saved_wizard_data['server_port'] = self.server_port_var.get()

        # Step 1 (software): Save EULA state, version, and server type
        if self.current_step == 1:
            if hasattr(self, 'eula_var'):
                self.saved_wizard_data['eula'] = self.eula_var.get()
            if hasattr(self, 'version_var') and self.version_var.get():
                self.saved_wizard_data['version'] = self.version_var.get()
            if hasattr(self, 'server_type_var'):
                self.saved_wizard_data['server_type'] = self.server_type_var.get()

        # Step 2 (performance): Save memory and other settings
        if self.current_step == 2:
            if hasattr(self, 'memory_var'):
                self.saved_wizard_data['advanced']['memory'] = self.memory_var.get()
            if hasattr(self, 'max_players_var'):
                self.saved_wizard_data['max_players'] = self.max_players_var.get()
            if hasattr(self, 'view_distance_var'):
                self.saved_wizard_data['view_distance'] = self.view_distance_var.get()
            if hasattr(self, 'gamemode_var'):
                self.saved_wizard_data['gamemode'] = self.gamemode_var.get()
            if hasattr(self, 'difficulty_var'):
                self.saved_wizard_data['difficulty'] = self.difficulty_var.get()

        # Step 3 (backups & security): Save backup and security settings
        if self.current_step == 3:
            if hasattr(self, 'auto_backup_var'):
                self.saved_wizard_data['auto_backup'] = self.auto_backup_var.get()
            if hasattr(self, 'backup_freq_var'):
                self.saved_wizard_data['backup_freq'] = self.backup_freq_var.get()
            if hasattr(self, 'max_backups_var'):
                self.saved_wizard_data['max_backups'] = self.max_backups_var.get()
            if hasattr(self, 'pvp_var'):
                self.saved_wizard_data['pvp'] = self.pvp_var.get()

            # Save whitelist settings
            if hasattr(self, 'whitelist_var'):
                self.saved_wizard_data['whitelist_enabled'] = self.whitelist_var.get()
                self.saved_wizard_data['whitelist_players'] = self.whitelist_players if hasattr(self, 'whitelist_players') else []

    def wizard_next_step(self):
        """Advance to the next wizard step"""
        if self.current_step < len(self.wizard_steps) - 1:
            self.wizard_show_step(self.current_step + 1)

    def wizard_previous_step(self):
        """Go back to the previous wizard step"""
        if self.current_step > 0:
            self.wizard_show_step(self.current_step - 1)

    def wizard_step_basic_info(self):
        """Step 1: Basic server information with validation"""
        # Create form
        self.wizard_content.grid_columnconfigure(1, weight=1)

        # Form title
        title = tki.CTkLabel(self.wizard_content, text="Basic Server Information",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")  # REDUCED PADDING

        # Error message area
        self.validation_error = tki.CTkLabel(self.wizard_content, text="", text_color="red")
        self.validation_error.grid(row=6, column=0, columnspan=2, padx=15, pady=(0, 5), sticky="w")  # REDUCED PADDING

        # Server name
        name_label = tki.CTkLabel(self.wizard_content, text="Server Name:")
        name_label.grid(row=1, column=0, padx=15, pady=8, sticky="w")  # REDUCED PADDING

        # Restore server name from saved data or use default
        server_name = "My Minecraft Server"
        if hasattr(self, 'saved_wizard_data') and 'server_name' in self.saved_wizard_data:
            server_name = self.saved_wizard_data['server_name']

        self.server_name_var = tki.StringVar(value=server_name)
        self.server_name_var.trace_add("write", self.validate_basic_info)
        self.server_name_entry = tki.CTkEntry(self.wizard_content, width=400,
                                            textvariable=self.server_name_var)
        self.server_name_entry.grid(row=1, column=1, padx=15, pady=8, sticky="ew")  # REDUCED PADDING

        # Server description (MOTD)
        desc_label = tki.CTkLabel(self.wizard_content, text="Server Description (MOTD):")
        desc_label.grid(row=2, column=0, padx=15, pady=8, sticky="w")  # REDUCED PADDING

        # Restore description from saved data or use default
        server_desc = "A Minecraft Server"
        if hasattr(self, 'saved_wizard_data') and 'server_desc' in self.saved_wizard_data:
            server_desc = self.saved_wizard_data['server_desc']

        self.server_desc_var = tki.StringVar(value=server_desc)
        self.server_desc_entry = tki.CTkEntry(self.wizard_content, width=400,
                                            textvariable=self.server_desc_var)
        self.server_desc_entry.grid(row=2, column=1, padx=15, pady=8, sticky="ew")  # REDUCED PADDING

        # Server ID (for proxies like Velocity)
        id_label = tki.CTkLabel(self.wizard_content, text="Server ID (for proxies):")
        id_label.grid(row=3, column=0, padx=15, pady=8, sticky="w")  # REDUCED PADDING

        # Restore server ID from saved data or use default
        server_id = "main"
        if hasattr(self, 'saved_wizard_data') and 'server_id' in self.saved_wizard_data:
            server_id = self.saved_wizard_data['server_id']

        self.server_id_var = tki.StringVar(value=server_id)
        self.server_id_var.trace_add("write", self.validate_basic_info)
        self.server_id_entry = tki.CTkEntry(self.wizard_content, width=400,
                                          textvariable=self.server_id_var)
        self.server_id_entry.grid(row=3, column=1, padx=15, pady=8, sticky="ew")  # REDUCED PADDING

        # Server port
        port_label = tki.CTkLabel(self.wizard_content, text="Server Port:")
        port_label.grid(row=4, column=0, padx=15, pady=8, sticky="w")  # REDUCED PADDING

        # Restore port from saved data or use default
        server_port = "25565"
        if hasattr(self, 'saved_wizard_data') and 'server_port' in self.saved_wizard_data:
            server_port = self.saved_wizard_data['server_port']

        self.server_port_var = tki.StringVar(value=server_port)
        self.server_port_var.trace_add("write", self.validate_basic_info)
        self.server_port_entry = tki.CTkEntry(self.wizard_content, width=400,
                                            textvariable=self.server_port_var)
        self.server_port_entry.grid(row=4, column=1, padx=15, pady=8, sticky="ew")  # REDUCED PADDING

        # Description text
        desc_text = "These basic settings define how your server appears to players and how it connects to networks."
        description = tki.CTkLabel(self.wizard_content, text=desc_text, wraplength=600)
        description.grid(row=5, column=0, columnspan=2, padx=15, pady=(15, 5), sticky="w")  # REDUCED PADDING

        # Run initial validation
        self.validate_basic_info()

    def validate_basic_info(self, *args):
        """Validate basic info fields and update UI accordingly"""
        valid = True
        error_msg = ""

        # Validate server name (not empty)
        server_name = self.server_name_var.get().strip()
        if not server_name:
            valid = False
            error_msg = "Server name cannot be empty"

        # Validate server ID (alphanumeric)
        server_id = self.server_id_var.get().strip()
        if not Validators.alphanumeric(server_id):
            valid = False
            error_msg = "Server ID must be alphanumeric"

        # Validate port (numeric, between 1024-65535)
        port = self.server_port_var.get().strip()
        if not Validators.number(port):
            valid = False
            error_msg = "Port must be a number"
        elif not (1024 <= int(port) <= 65535):
            valid = False
            error_msg = "Port must be between 1024 and 65535"

        # Update validation error message
        if hasattr(self, 'validation_error'):
            self.validation_error.configure(text=error_msg)

        # Update next button state
        if hasattr(self, 'next_btn'):
            self.next_btn.configure(state="normal" if valid else "disabled")

        return valid

    def wizard_step_software(self):
        """Step 2: Server software selection"""
        # Create form
        self.wizard_content.grid_columnconfigure(1, weight=1)
        self.wizard_content.grid_rowconfigure(6, weight=1)  # Make the version selector expandable

        # Form title
        title = tki.CTkLabel(self.wizard_content, text="Server Software",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")  # REDUCED PADDING

        # Server type selection
        type_label = tki.CTkLabel(self.wizard_content, text="Server Software:")
        type_label.grid(row=1, column=0, padx=15, pady=8, sticky="w")  # REDUCED PADDING

        # Get saved server type if available
        server_type = "purpur"  # Default to purpur
        if hasattr(self, 'saved_wizard_data') and 'server_type' in self.saved_wizard_data:
            server_type = self.saved_wizard_data['server_type']

        self.server_type_var = tki.StringVar(value=server_type)

        paper_radio = tki.CTkRadioButton(self.wizard_content, text="Paper",
                                       variable=self.server_type_var, value="paper",
                                       command=self.fetch_server_versions)
        paper_radio.grid(row=1, column=1, padx=15, pady=4, sticky="w")  # REDUCED PADDING

        paper_desc = tki.CTkLabel(self.wizard_content,
                                text="High performance Spigot fork with additional optimizations and features")
        paper_desc.grid(row=2, column=1, padx=30, pady=(0, 8), sticky="w")  # REDUCED PADDING

        purpur_radio = tki.CTkRadioButton(self.wizard_content, text="Purpur (Recommended)",
                                        variable=self.server_type_var, value="purpur",
                                        command=self.fetch_server_versions)
        purpur_radio.grid(row=3, column=1, padx=15, pady=4, sticky="w")  # REDUCED PADDING

        purpur_desc = tki.CTkLabel(self.wizard_content,
                                 text="Fork of Paper with additional features and optimizations")
        purpur_desc.grid(row=4, column=1, padx=30, pady=(0, 8), sticky="w")  # REDUCED PADDING

        # Game version selection
        version_label = tki.CTkLabel(self.wizard_content, text="Minecraft Version:")
        version_label.grid(row=5, column=0, padx=15, pady=8, sticky="nw")  # REDUCED PADDING

        # Version selection frame with loading indicator
        self.version_frame = tki.CTkScrollableFrame(self.wizard_content, height=180)  # REDUCED HEIGHT
        self.version_frame.grid(row=5, column=1, padx=15, pady=8, sticky="nsew")  # REDUCED PADDING

        self.version_loading = tki.CTkLabel(self.version_frame, text="Loading versions...")
        self.version_loading.pack(pady=8)  # REDUCED PADDING

        # Eula acceptance - moved to bottom for better visibility
        eula_frame = tki.CTkFrame(self.wizard_content)
        eula_frame.grid(row=7, column=0, columnspan=2, padx=15, pady=(8, 5), sticky="ew")  # REDUCED PADDING

        # Check for saved EULA acceptance
        eula_accepted = False
        if hasattr(self, 'saved_wizard_data') and 'eula' in self.saved_wizard_data:
            eula_accepted = self.saved_wizard_data['eula']

        self.eula_var = tki.BooleanVar(value=eula_accepted)
        # Add trace to update next button state when EULA is toggled
        self.eula_var.trace_add("write", self.update_eula_state)

        self.eula_check = tki.CTkCheckBox(eula_frame,
                                        text="I agree to the Minecraft End User License Agreement",
                                        variable=self.eula_var)
        self.eula_check.pack(side=tki.LEFT, padx=8, pady=8)  # REDUCED PADDING

        eula_link = tki.CTkButton(eula_frame, text="View EULA",
                                command=lambda: self.open_url("https://www.minecraft.net/en-us/eula"),
                                width=100)
        eula_link.pack(side=tki.RIGHT, padx=8, pady=8)  # REDUCED PADDING

        # EULA requirement label in red
        self.eula_required = tki.CTkLabel(
            eula_frame,
            text="* You must accept the EULA to continue",
            text_color="red"
        )
        self.eula_required.pack(side=tki.BOTTOM, padx=8, pady=(0, 3))  # REDUCED PADDING

        # Fetch versions
        self.fetch_server_versions()

        # Update next button state based on initial EULA setting
        self.update_eula_state()

    def update_eula_state(self, *args):
        """Update next button state based on EULA acceptance"""
        if hasattr(self, 'next_btn'):
            self.next_btn.configure(state="normal" if self.eula_var.get() else "disabled")

        # Update the visibility of the EULA requirement text
        if hasattr(self, 'eula_required'):
            self.eula_required.configure(text="" if self.eula_var.get() else "* You must accept the EULA to continue")

    def fetch_server_versions(self):
        """Fetch available versions from server software API"""
        import threading

        # Clear existing versions
        for widget in self.version_frame.winfo_children():
            widget.destroy()

        self.version_loading = tki.CTkLabel(self.version_frame, text="Loading versions...")
        self.version_loading.pack(pady=10)

        # Start a thread to fetch versions
        threading.Thread(target=self._fetch_versions_thread, daemon=True).start()

    def _fetch_versions_thread(self):
        """Thread worker to fetch server versions"""
        import requests
        import json
        from concurrent.futures import ThreadPoolExecutor

        try:
            server_type = self.server_type_var.get()
            versions = []

            if server_type == "paper":
                # Paper API
                response = requests.get("https://api.papermc.io/v2/projects/paper")
                if response.status_code == 200:
                    data = response.json()
                    versions = data.get("versions", [])
                    versions.reverse()  # Newest first
            elif server_type == "purpur":
                # Purpur API
                response = requests.get("https://api.purpurmc.org/v2/purpur")
                if response.status_code == 200:
                    data = response.json()
                    versions = data.get("versions", [])
                    versions.reverse()  # Newest first

            # Update UI in main thread
            self.after(0, lambda: self._update_versions_ui(versions))

        except Exception as e:
            print(f"Error fetching versions: {e}")
            self.after(0, lambda: self._update_versions_ui([]))

    def _update_versions_ui(self, versions):
        """Update versions UI with fetched versions"""
        # Clear loading indicator
        for widget in self.version_frame.winfo_children():
            widget.destroy()

        if not versions:
            error_label = tki.CTkLabel(self.version_frame,
                                     text="Failed to fetch versions. Please check your internet connection.")
            error_label.pack(pady=10)
            return

        # Version selection
        self.version_var = tki.StringVar()

        # Create scrollable frame for versions
        versions_scroll = tki.CTkScrollableFrame(self.version_frame, height=200)
        versions_scroll.pack(fill=tki.BOTH, expand=True)

        # Recent versions at the top with recommendation
        recent_versions = versions[:6]

        # Recommended version (newest)
        if versions:
            self.version_var.set(versions[0])

        # Add version radio buttons
        for version in recent_versions:
            is_recommended = version == recent_versions[0]
            label_text = f"{version} {'(Recommended)' if is_recommended else ''}"

            radio = tki.CTkRadioButton(versions_scroll,
                                     text=label_text,
                                     variable=self.version_var,
                                     value=version)
            radio.pack(anchor="w", padx=10, pady=5)

        # Separator
        if len(versions) > len(recent_versions):
            separator = tki.CTkFrame(versions_scroll, height=1)
            separator.pack(fill="x", padx=10, pady=10)

            older_label = tki.CTkLabel(versions_scroll, text="Older versions:")
            older_label.pack(anchor="w", padx=10, pady=5)

            # Add older versions
            for version in versions[len(recent_versions):]:
                radio = tki.CTkRadioButton(versions_scroll,
                                         text=version,
                                         variable=self.version_var,
                                         value=version)
                radio.pack(anchor="w", padx=10, pady=5)

    def wizard_step_performance(self):
        """Step 3: Server performance settings"""
        # Create form
        self.wizard_content.grid_columnconfigure(1, weight=1)

        # Form title
        title = tki.CTkLabel(self.wizard_content, text="Server Performance",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")  # REDUCED PADDING

        # Memory allocation
        memory_label = tki.CTkLabel(self.wizard_content, text="Memory Allocation (MB):")
        memory_label.grid(row=1, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get memory value from saved data or use default
        memory_value = "2048"
        if hasattr(self, 'saved_wizard_data') and 'memory' in self.saved_wizard_data:
            memory_value = self.saved_wizard_data['advanced']['memory']

        self.memory_var = tki.StringVar(value=memory_value)
        self.memory_entry = tki.CTkEntry(self.wizard_content, width=200,
                                       textvariable=self.memory_var)
        self.memory_entry.grid(row=1, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        memory_presets = tki.CTkFrame(self.wizard_content)
        memory_presets.grid(row=2, column=1, padx=10, pady=(0, 6), sticky="w")  # REDUCED PADDING

        # Memory preset buttons
        preset_values = [
            ("1 GB", "1024"),
            ("2 GB", "2048"),
            ("4 GB", "4096"),
            ("8 GB", "8192"),
            ("16 GB", "16384")
        ]

        for i, (label, value) in enumerate(preset_values):
            btn = tki.CTkButton(memory_presets, text=label, width=60,  # REDUCED BUTTON WIDTH
                              command=lambda v=value: self.memory_var.set(v))
            btn.grid(row=0, column=i, padx=2, pady=2)  # REDUCED PADDING

        # Memory recommendation text
        import psutil
        total_memory = psutil.virtual_memory().total // (1024 * 1024)  # Total RAM in MB
        recommended = min(4096, total_memory // 2)  # 50% of RAM or max 4GB

        memory_desc = tki.CTkLabel(self.wizard_content,
                                 text=f"Recommended: {recommended} MB (Your system has {total_memory} MB)")
        memory_desc.grid(row=3, column=1, padx=10, pady=(0, 10), sticky="w")  # REDUCED PADDING

        # Max players
        players_label = tki.CTkLabel(self.wizard_content, text="Maximum Players:")
        players_label.grid(row=4, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get max players from saved data or use default
        max_players = "20"
        if hasattr(self, 'saved_wizard_data') and 'max_players' in self.saved_wizard_data:
            max_players = self.saved_wizard_data['max_players']

        self.max_players_var = tki.StringVar(value=max_players)
        self.max_players_entry = tki.CTkEntry(self.wizard_content, width=200,
                                            textvariable=self.max_players_var)
        self.max_players_entry.grid(row=4, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # View distance
        view_label = tki.CTkLabel(self.wizard_content, text="View Distance:")
        view_label.grid(row=5, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get view distance from saved data or use default
        view_distance = "10"
        if hasattr(self, 'saved_wizard_data') and 'view_distance' in self.saved_wizard_data:
            view_distance = self.saved_wizard_data['view_distance']

        self.view_distance_var = tki.StringVar(value=view_distance)
        self.view_distance_entry = tki.CTkEntry(self.wizard_content, width=200,
                                              textvariable=self.view_distance_var)
        self.view_distance_entry.grid(row=5, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Gamemode
        gamemode_label = tki.CTkLabel(self.wizard_content, text="Default Gamemode:")
        gamemode_label.grid(row=6, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get gamemode from saved data or use default
        gamemode = "survival"
        if hasattr(self, 'saved_wizard_data') and 'gamemode' in self.saved_wizard_data:
            gamemode = self.saved_wizard_data['gamemode']

        self.gamemode_var = tki.StringVar(value=gamemode)
        self.gamemode_combo = tki.CTkComboBox(self.wizard_content, width=200,
                                            values=["survival", "creative", "adventure", "spectator"],
                                            variable=self.gamemode_var)
        self.gamemode_combo.grid(row=6, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Difficulty
        difficulty_label = tki.CTkLabel(self.wizard_content, text="Difficulty:")
        difficulty_label.grid(row=7, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get difficulty from saved data or use default
        difficulty = "normal"
        if hasattr(self, 'saved_wizard_data') and 'difficulty' in self.saved_wizard_data:
            difficulty = self.saved_wizard_data['difficulty']

        self.difficulty_var = tki.StringVar(value=difficulty)
        self.difficulty_combo = tki.CTkComboBox(self.wizard_content, width=200,
                                              values=["peaceful", "easy", "normal", "hard"],
                                              variable=self.difficulty_var)
        self.difficulty_combo.grid(row=7, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Description text
        desc_text = "These settings control the performance and gameplay of your server. Higher values may require more resources."
        description = tki.CTkLabel(self.wizard_content, text=desc_text, wraplength=600)
        description.grid(row=8, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")  # REDUCED PADDING

    def wizard_step_backups(self):
        """Step 4: Backup and security settings"""
        # Create form
        self.wizard_content.grid_columnconfigure(1, weight=1)

        # Form title
        title = tki.CTkLabel(self.wizard_content, text="Backups & Security",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")  # REDUCED PADDING

        # Auto backup settings
        backup_label = tki.CTkLabel(self.wizard_content, text="Automatic Backups:")
        backup_label.grid(row=1, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get backup settings from saved data or use defaults
        auto_backup = True
        if hasattr(self, 'saved_wizard_data') and 'auto_backup' in self.saved_wizard_data:
            auto_backup = self.saved_wizard_data['auto_backup']

        self.auto_backup_var = tki.BooleanVar(value=auto_backup)
        auto_backup_switch = tki.CTkSwitch(self.wizard_content, text="Enable automatic backups",
                                         variable=self.auto_backup_var)
        auto_backup_switch.grid(row=1, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Backup frequency
        freq_label = tki.CTkLabel(self.wizard_content, text="Backup Frequency:")
        freq_label.grid(row=2, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get backup frequency from saved data or use default
        backup_freq = "24"
        if hasattr(self, 'saved_wizard_data') and 'backup_freq' in self.saved_wizard_data:
            backup_freq = self.saved_wizard_data['backup_freq']

        self.backup_freq_var = tki.StringVar(value=backup_freq)
        backup_freq_combo = tki.CTkComboBox(self.wizard_content, width=180,  # REDUCED WIDTH
                                           values=["6", "12", "24", "48", "72"],
                                           variable=self.backup_freq_var)
        backup_freq_combo.grid(row=2, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        backup_freq_desc = tki.CTkLabel(self.wizard_content, text="Hours between automatic backups")
        backup_freq_desc.grid(row=3, column=1, padx=10, pady=(0, 6), sticky="w")  # REDUCED PADDING

        # Max backups
        max_backups_label = tki.CTkLabel(self.wizard_content, text="Max Backups:")
        max_backups_label.grid(row=4, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get max backups from saved data or use default
        max_backups = "10"
        if hasattr(self, 'saved_wizard_data') and 'max_backups' in self.saved_wizard_data:
            max_backups = self.saved_wizard_data['max_backups']

        self.max_backups_var = tki.StringVar(value=max_backups)
        max_backups_combo = tki.CTkComboBox(self.wizard_content, width=180,  # REDUCED WIDTH
                                           values=["5", "10", "20", "30", "All"],
                                           variable=self.max_backups_var)
        max_backups_combo.grid(row=4, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        max_backups_desc = tki.CTkLabel(self.wizard_content, text="Maximum number of backups to keep (oldest will be deleted)")
        max_backups_desc.grid(row=5, column=1, padx=10, pady=(0, 6), sticky="w")  # REDUCED PADDING

        # Security section
        security_title = tki.CTkLabel(self.wizard_content, text="Security",
                                    font=tki.CTkFont(size=16, weight="bold"))
        security_title.grid(row=6, column=0, columnspan=2, padx=10, pady=(15, 8), sticky="w")  # REDUCED PADDING

        # Online mode information (instead of toggle)
        online_info = tki.CTkLabel(self.wizard_content,
                                  text="This server will run in online mode, requiring players to have a valid Minecraft account.",
                                  wraplength=500)
        online_info.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="w")  # REDUCED PADDING

        # PvP settings
        pvp_label = tki.CTkLabel(self.wizard_content, text="PvP:")
        pvp_label.grid(row=8, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get PvP setting from saved data or use default
        pvp_enabled = True
        if hasattr(self, 'saved_wizard_data') and 'pvp' in self.saved_wizard_data:
            pvp_enabled = self.saved_wizard_data['pvp']

        self.pvp_var = tki.BooleanVar(value=pvp_enabled)
        pvp_switch = tki.CTkSwitch(self.wizard_content, text="Enable player vs. player combat",
                                 variable=self.pvp_var)
        pvp_switch.grid(row=8, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Whitelist section
        whitelist_label = tki.CTkLabel(self.wizard_content, text="Whitelist:")
        whitelist_label.grid(row=9, column=0, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Get whitelist settings from saved data
        whitelist_enabled = False
        if hasattr(self, 'saved_wizard_data') and 'whitelist_enabled' in self.saved_wizard_data:
            whitelist_enabled = self.saved_wizard_data['whitelist_enabled']

        self.whitelist_var = tki.BooleanVar(value=whitelist_enabled)
        whitelist_switch = tki.CTkSwitch(self.wizard_content, text="Enable whitelist (only listed players can join)",
                                       variable=self.whitelist_var,
                                       command=self.update_whitelist_frame)
        whitelist_switch.grid(row=9, column=1, padx=10, pady=6, sticky="w")  # REDUCED PADDING

        # Load whitelist players from saved data
        if hasattr(self, 'saved_wizard_data') and 'whitelist_players' in self.saved_wizard_data:
            self.whitelist_players = self.saved_wizard_data['whitelist_players']
        else:
            self.whitelist_players = []

        # Whitelist players frame (initially hidden)
        self.whitelist_frame = tki.CTkFrame(self.wizard_content)
        self.whitelist_frame.grid(row=10, column=0, columnspan=2, padx=10, pady=6, sticky="ew")  # REDUCED PADDING

        # Scrollable list for whitelist players
        self.whitelist_list_frame = tki.CTkScrollableFrame(self.whitelist_frame, height=130, width=400)  # REDUCED HEIGHT
        self.whitelist_list_frame.pack(fill=tki.X, expand=True, pady=6)  # REDUCED PADDING

        # Buttons for managing whitelist
        buttons_frame = tki.CTkFrame(self.whitelist_frame)
        buttons_frame.pack(fill=tki.X, pady=(0, 6))  # REDUCED PADDING

        add_player_btn = tki.CTkButton(buttons_frame, text="Add Player",
                                     command=self.add_whitelist_player, width=110)  # REDUCED WIDTH
        add_player_btn.pack(side=tki.LEFT, padx=8, pady=3)  # REDUCED PADDING

        remove_player_btn = tki.CTkButton(buttons_frame, text="Remove Selected",
                                        command=self.remove_whitelist_player, width=110)  # REDUCED WIDTH
        remove_player_btn.pack(side=tki.LEFT, padx=8, pady=3)  # REDUCED PADDING

        # Update whitelist frame visibility based on initial value
        self.update_whitelist_frame()

        # Make sure online mode is always true (hidden variable)
        self.online_mode_var = tki.BooleanVar(value=True)

    def update_whitelist_frame(self):
        """Show or hide whitelist configuration based on whitelist toggle"""
        if hasattr(self, 'whitelist_var') and hasattr(self, 'whitelist_frame'):
            if self.whitelist_var.get():
                self.whitelist_frame.grid()
                self.update_whitelist_list()
            else:
                self.whitelist_frame.grid_remove()

    def update_whitelist_list(self):
        """Update the whitelist players list display"""
        # Clear existing content
        for widget in self.whitelist_list_frame.winfo_children():
            widget.destroy()

        # Show whitelist players or a message if empty
        if not self.whitelist_players:
            empty_label = tki.CTkLabel(self.whitelist_list_frame,
                                    text="No players in whitelist. Add players below.")
            empty_label.pack(pady=20)
        else:
            # Add each player to the list with selection capability
            for i, player in enumerate(self.whitelist_players):
                player_frame = tki.CTkFrame(self.whitelist_list_frame)
                player_frame.pack(fill=tki.X, pady=2)

                # Use a variable to track selection
                var = tki.IntVar(value=0)

                # Create a checkbutton for selection
                checkbtn = tki.CTkCheckBox(player_frame, text="", variable=var)
                checkbtn.pack(side=tki.LEFT, padx=5)

                # Store the selection variable in the frame for later access
                player_frame.selection_var = var

                # Player name label
                player_label = tki.CTkLabel(player_frame, text=player)
                player_label.pack(side=tki.LEFT, padx=5, fill=tki.X, expand=True)

    def add_whitelist_player(self):
        """Open dialog to add a player to the whitelist"""
        dialog = tki.CTkInputDialog(text="Enter Minecraft username:", title="Add Player to Whitelist")
        player_name = dialog.get_input()

        if player_name:
            # Validate username (alphanumeric with underscores, 3-16 chars)
            import re
            if not re.match(r'^[a-zA-Z0-9_]{3,16}$', player_name):
                self.show_notification("Invalid Minecraft username. Must be 3-16 alphanumeric characters or underscores.", "error")
                return

            # Add to whitelist if not already there
            if player_name not in self.whitelist_players:
                self.whitelist_players.append(player_name)
                self.update_whitelist_list()
            else:
                self.show_notification("Player is already in the whitelist.")

    def remove_whitelist_player(self):
        """Remove selected players from whitelist"""
        # Find all selected players
        selected_players = []

        for widget in self.whitelist_list_frame.winfo_children():
            if hasattr(widget, 'selection_var') and widget.selection_var.get() == 1:
                # Find player name in the child widgets (should be a label)
                for child in widget.winfo_children():
                    if isinstance(child, tki.CTkLabel):
                        selected_players.append(child.cget("text"))

        # Remove selected players
        if selected_players:
            for player in selected_players:
                if player in self.whitelist_players:
                    self.whitelist_players.remove(player)

            self.update_whitelist_list()
        else:
            self.show_notification("No players selected for removal.")

    def wizard_step_summary(self):  # sourcery skip: low-code-quality
        """Step 5: Summary of server settings before creation"""
        # Create scrollable frame for summary
        self.wizard_content.grid_columnconfigure(0, weight=1)
        self.wizard_content.grid_rowconfigure(0, weight=1)

        # Use grid instead of pack for more consistent positioning
        summary_frame = tki.CTkFrame(self.wizard_content)
        summary_frame.pack(padx=10, pady=10, fill='both', expand=True)

        # Add title
        title = tki.CTkLabel(summary_frame, text="Server Configuration Summary",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.pack(anchor="w", pady=(5, 15), padx=5)  # REDUCED PADDING

        # Basic Information
        basic_title = tki.CTkLabel(summary_frame, text="Basic Information",
                                 font=tki.CTkFont(size=16, weight="bold"))
        basic_title.pack(anchor="w", pady=(8, 4), padx=5)  # REDUCED PADDING

        # Save current settings to ensure we have the latest
        if not hasattr(self, 'saved_wizard_data'):
            self.saved_wizard_data = {}

        # Use values from saved data or current variables
        if hasattr(self, 'server_name_var'):
            self.saved_wizard_data['server_name'] = self.server_name_var.get()
            self.saved_wizard_data['server_desc'] = self.server_desc_var.get()
            self.saved_wizard_data['server_id'] = self.server_id_var.get()
            self.saved_wizard_data['server_port'] = self.server_port_var.get()

        basic_info = {
            "Server Name": self.saved_wizard_data.get('server_name', "My Minecraft Server"),
            "Description": self.saved_wizard_data.get('server_desc', "A Minecraft Server"),
            "Server ID": self.saved_wizard_data.get('server_id', "main"),
            "Port": self.saved_wizard_data.get('server_port', "25565")
        }

        for label, value in basic_info.items():
            info_text = f"{label}: {value}"
            info_label = tki.CTkLabel(summary_frame, text=info_text)
            info_label.pack(anchor="w", padx=15)  # REDUCED PADDING

        # Software Information
        software_title = tki.CTkLabel(summary_frame, text="Server Software",
                                    font=tki.CTkFont(size=16, weight="bold"))
        software_title.pack(anchor="w", pady=(15, 4), padx=5)  # REDUCED PADDING

        software_info = {
            "Software Type": self.server_type_var.get().title() if hasattr(self, 'server_type_var') else "Purpur",
            "Minecraft Version": self.version_var.get() if hasattr(self, 'version_var') else "Unknown",
            "EULA Accepted": "Yes" if hasattr(self, 'eula_var') and self.eula_var.get() else "No"
        }

        for label, value in software_info.items():
            info_text = f"{label}: {value}"
            info_label = tki.CTkLabel(summary_frame, text=info_text)
            info_label.pack(anchor="w", padx=15)  # REDUCED PADDING

        # Performance Settings
        perf_title = tki.CTkLabel(summary_frame, text="Performance Settings",
                                font=tki.CTkFont(size=16, weight="bold"))
        perf_title.pack(anchor="w", pady=(15, 4), padx=5)  # REDUCED PADDING

        perf_info = {
            "Memory Allocation": f"{self.memory_var.get() if hasattr(self, 'memory_var') else '2048'} MB",
            "Maximum Players": self.max_players_var.get() if hasattr(self, 'max_players_var') else "20",
            "View Distance": self.view_distance_var.get() if hasattr(self, 'view_distance_var') else "10",
            "Default Gamemode": self.gamemode_var.get().title() if hasattr(self, 'gamemode_var') else "Survival",
            "Difficulty": self.difficulty_var.get().title() if hasattr(self, 'difficulty_var') else "Normal"
        }

        for label, value in perf_info.items():
            info_text = f"{label}: {value}"
            info_label = tki.CTkLabel(summary_frame, text=info_text)
            info_label.pack(anchor="w", padx=15, pady=2)  # REDUCED PADDING

        # Backup Settings
        backup_title = tki.CTkLabel(summary_frame, text="Backup Settings",
                                  font=tki.CTkFont(size=16, weight="bold"))
        backup_title.pack(anchor="w", pady=(15, 4), padx=5)  # REDUCED PADDING

        # Safely access backup settings
        auto_backup = self.auto_backup_var.get() if hasattr(self, 'auto_backup_var') else True
        backup_freq = self.backup_freq_var.get() if hasattr(self, 'backup_freq_var') else "24"
        max_backups = self.max_backups_var.get() if hasattr(self, 'max_backups_var') else "10"

        backup_info = {
            "Automatic Backups": "Enabled" if auto_backup else "Disabled",
            "Backup Frequency": f"Every {backup_freq} hours" if auto_backup else "N/A",
            "Maximum Backups": max_backups if auto_backup else "N/A"
        }

        for label, value in backup_info.items():
            info_text = f"{label}: {value}"
            info_label = tki.CTkLabel(summary_frame, text=info_text)
            info_label.pack(anchor="w", padx=15, pady=2)  # REDUCED PADDING

        # Security Settings
        security_title = tki.CTkLabel(summary_frame, text="Security Settings",
                                    font=tki.CTkFont(size=16, weight="bold"))
        security_title.pack(anchor="w", pady=(15, 4), padx=5)  # REDUCED PADDING

        # Show whitelist info
        whitelist_enabled = self.whitelist_var.get() if hasattr(self, 'whitelist_var') else False
        whitelist_players = self.whitelist_players if hasattr(self, 'whitelist_players') else []
        whitelist_players_count = len(whitelist_players)
        pvp_enabled = self.pvp_var.get() if hasattr(self, 'pvp_var') else True

        security_info = {
            "PvP": "Enabled" if pvp_enabled else "Disabled",
            "Whitelist": f"{'Enabled' if whitelist_enabled else 'Disabled'} ({whitelist_players_count} players)"
        }

        for label, value in security_info.items():
            info_text = f"{label}: {value}"
            info_label = tki.CTkLabel(summary_frame, text=info_text)
            info_label.pack(anchor="w", padx=15, pady=2)  # REDUCED PADDING

        # Whitelist players list if enabled
        if whitelist_enabled and whitelist_players_count > 0:
            whitelist_players_title = tki.CTkLabel(summary_frame, text="Whitelist Players:",
                                                font=tki.CTkFont(weight="bold"))
            whitelist_players_title.pack(anchor="w", padx=15, pady=(8, 4))  # REDUCED PADDING

            players_text = ", ".join(whitelist_players)
            whitelist_players_label = tki.CTkLabel(summary_frame, text=players_text, wraplength=400)
            whitelist_players_label.pack(anchor="w", padx=20)  # REDUCED PADDING

        # Final note
        final_note = tki.CTkLabel(summary_frame,
                                text="Click 'Create Server' to download and configure the server with these settings.",
                                wraplength=500)
        final_note.pack(anchor="w", pady=(15, 0), padx=5)  # REDUCED PADDING

    # Create server
    def create_server(self):
        """Create the Minecraft server with the specified settings"""
        # Check if EULA is accepted
        if not self.eula_var.get():
            self.show_notification("You must accept the Minecraft EULA to create a server", "error")
            return

        # Create progress window
        self.progress_window = tki.CTkToplevel(self)
        self.progress_window.title("Creating Server")
        self.progress_window.geometry("500x300")
        self.progress_window.transient(self)
        self.progress_window.grab_set()

        # Configure grid
        self.progress_window.grid_columnconfigure(0, weight=1)

        # Title
        title = tki.CTkLabel(self.progress_window, text="Creating Minecraft Server",
                           font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Status label
        self.status_label = tki.CTkLabel(self.progress_window, text="Initializing...")
        self.status_label.grid(row=1, column=0, padx=20, pady=(0, 10))

        # Progress bar
        self.progress_bar = tki.CTkProgressBar(self.progress_window, width=400)
        self.progress_bar.grid(row=2, column=0, padx=20, pady=(0, 10))
        self.progress_bar.set(0)

        # Detail log
        log_frame = tki.CTkFrame(self.progress_window)
        log_frame.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        self.progress_window.grid_rowconfigure(3, weight=1)

        self.log_text = tki.CTkTextbox(log_frame, height=120, width=400)
        self.log_text.pack(fill=tki.BOTH, expand=True, padx=10, pady=10)

        # Start server creation in a separate thread
        threading.Thread(target=self._create_server_thread, daemon=True).start()

    def _create_server_thread(
            self,
            server_name=None,
            server_type=None,
            mc_version=None,
            server_port=None,
            memory=4096,
            backup=None
        ):  # sourcery skip: extract-method, low-code-quality
        """Thread to handle server creation process"""

        try:
            if '-nogui' not in self.args:
                # Get values from wizard
                server_name = self.server_name_var.get()
                server_type = self.server_type_var.get()
                mc_version = self.version_var.get()
            # Ensure server_name is not None before iteration
            server_name = server_name or "server"
            server_folder_name = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in server_name)

            # Update status
            self._update_status("Creating server directory", 0.01)

            # Create server directory
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "servers"))
            server_dir = os.path.join(base_dir, server_folder_name)

            # Check if directory exists
            if os.path.exists(server_dir):
                # Append number to make it unique
                counter = 1
                while os.path.exists(f"{server_dir}_{counter}"):
                    counter += 1
                server_dir = f"{server_dir}_{counter}"
                server_folder_name = f"{server_folder_name}_{counter}"

            # Create the directory
            os.makedirs(server_dir)

            # Update status
            self._update_status(f"Downloading {server_type} {mc_version}", 0.02)

            # Download server jar
            jar_url = self._get_download_url(server_type, mc_version)
            jar_path = os.path.join(server_dir, f"{server_type}-{mc_version}.jar")

            response = requests.get(jar_url, stream=True)
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            with open(jar_path, 'wb') as f:
                for data in response.iter_content(chunk_size=4096):
                    f.write(data)
                    downloaded += len(data)
                    if total_size > 0:
                        progress = 0.02 + (downloaded / total_size) * 0.80
                        self._update_status(f"Downloading: {downloaded / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB", progress)

            # Update status
            self._update_status("Creating server.properties", 0.85)

            # Create server.properties
            self._create_server_properties(server_dir)

            # Update status
            self._update_status("Creating eula.txt", 0.90)

            # Create eula.txt
            with open(os.path.join(server_dir, "eula.txt"), "w") as f:
                f.write("# Generated by MC Manager\n")
                f.write("eula=true\n")

            # Update status
            self._update_status("Creating server configuration", 0.98)

            if '-nogui' not in self.args:
                # Save server config
                config = {
                    "name": server_name,
                    "description": self.server_desc_var.get(),
                    "server_id": self.server_id_var.get(),
                    "port": self.server_port_var.get(),
                    "server_type": server_type,
                    "version": mc_version,
                    "memory": self.memory_var.get(),
                    "backup": {
                        "enabled": self.auto_backup_var.get(),
                        "frequency": int(self.backup_freq_var.get()),
                        "max_backups": self.max_backups_var.get()
                    }
                }

            else:
                config = {
                    "name": server_name,
                    "description": "No description",
                    "server_id": server_name,
                    "port": server_port,
                    "server_type": server_type,
                    "version": mc_version,
                    "memory": memory,
                    "backup": {
                        "enabled": bool(backup) if backup else False,
                        "frequency": float(backup.split('-')[0]) if backup else 24.0,
                        "max_backups": backup.split('-')[1] if backup else 10
                    }
                }

            with open(os.path.join(server_dir, "server_config.json"), "w") as f:
                json.dump(config, f, indent=2)

            # Create directories
            os.makedirs(os.path.join(server_dir, "backups"), exist_ok=True)
            os.makedirs(os.path.join(server_dir, "plugins"), exist_ok=True)

            # Update status
            self._update_status("Server creation complete!", 1.0)

            time.sleep(0.3)

            self.servers = self.get_server_list()
            self.change_server(server_name)

            if '-nogui' not in self.args:
                # Add log entry
                self._add_log(f"Server '{server_name}' created successfully in {server_dir}")
                self._add_log("You can now start the server from the main interface.")

                # Wait a moment for user to see completion
                time.sleep(2)

                # Close progress window and refresh UI
                self.after(0, self._server_creation_completed, server_folder_name)

        except Exception as e:
            if '-nogui' not in self.args:
                self._add_log(f"Error: {str(e)}")
                self._update_status(f"Error: {str(e)}", 0)
                self.after(0, lambda: self.show_notification(f"Server creation failed: {str(e)}", "error"))
            else:
                print(f'Error: {e}')

    def _server_creation_completed(self, server_name):
        """Called when server creation is complete"""
        # Close progress window
        if hasattr(self, 'progress_window') and self.progress_window:
            self.progress_window.destroy()

        # Show success message
        self.show_notification(f"Server '{server_name}' created successfully!")

        # Refresh UI
        for widget in self.winfo_children():
            widget.destroy()

        # Re-initialize the main UI
        self.servers = self.get_server_list()
        self.initialize_main_ui()

        # Select the newly created server
        if server_name in self.servers:
            self.server_option.set(server_name)
            self.change_server(server_name)

    def _update_status(self, message, progress_value):
        """Update progress status"""
        def update_ui():
            self.status_label.configure(text=message)
            self.progress_bar.set(progress_value)
            self._add_log(message)

        if '-nogui' not in self.args:
            self.after(0, update_ui)
        else:
            print(message, f'{progress_value*100:.2f}%')

    def _add_log(self, message):
        """Add message to log"""
        def update_log():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{message}\n")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")

        self.after(0, update_log)

    def _get_download_url(self, server_type, mc_version):
        """Get download URL for the server jar"""
        import requests

        if server_type == "paper":
            # Get build info from Paper API
            api_url = f"https://api.papermc.io/v2/projects/paper/versions/{mc_version}"
            response = requests.get(api_url)
            builds = response.json()["builds"]
            latest_build = builds[-1]  # Get the latest build

            return f"https://api.papermc.io/v2/projects/paper/versions/{mc_version}/builds/{latest_build}/downloads/paper-{mc_version}-{latest_build}.jar"

        elif server_type == "purpur":
            # Purpur has a simpler versioning scheme
            return f"https://api.purpurmc.org/v2/purpur/{mc_version}/latest/download"

        raise ValueError(f"Unsupported server type: {server_type}")

    def _create_server_properties(self, server_dir):
        """Create server.properties file with wizard settings"""
        if '-nogui' not in self.args and '-install' not in self.args:
            properties = {
                "server-port": self.server_port_var.get(),
                "motd": self.server_desc_var.get(),
                "max-players": self.max_players_var.get(),
                "view-distance": self.view_distance_var.get(),
                "gamemode": self.gamemode_var.get(),
                "difficulty": self.difficulty_var.get(),
                "online-mode": "true",  # Always set to true
                "pvp": str(self.pvp_var.get()).lower(),
                "white-list": str(self.whitelist_var.get()).lower(),  # Add whitelist setting
                "enforce-whitelist": str(self.whitelist_var.get()).lower(),  # Add enforce whitelist
                "enable-command-block": "false",
                "spawn-protection": "0",
                "allow-nether": "true",
                "spawn-monsters": "true",
                "spawn-animals": "true",
                "spawn-npcs": "true",
                "hardcore": "false",
                "level-name": "world"
            }

            # Write the properties file
            with open(os.path.join(server_dir, "server.properties"), "w") as f:
                f.write("# Generated by MC Manager\n")
                for key, value in properties.items():
                    f.write(f"{key}={value}\n")

            # Create whitelist.json if whitelist is enabled
            if (hasattr(self, 'whitelist_var')
                    and self.whitelist_var.get()
                    and hasattr(self, 'whitelist_players')
                    and self.whitelist_players
                ):
                whitelist = [{"name": player, "uuid": ""} for player in self.whitelist_players]
                with open(os.path.join(server_dir, "whitelist.json"), "w") as f:
                    json.dump(whitelist, f, indent=2)

    # Import server
    def import_existing_server(self):
        """Import an existing server directory"""

        # Ask user to select directory containing their server
        source_dir = filedialog.askdirectory(title="Select Server Directory")

        if not source_dir:
            return

        # Check if directory contains server files
        server_jar_exists = any(f.endswith('.jar') for f in os.listdir(source_dir))

        if not server_jar_exists:
            self.show_notification("No server jar files found in the selected directory.", "error")
            return

        # Ask for server name
        self.import_dialog = tki.CTkToplevel(self)
        self.import_dialog.title("Import Server")
        self.import_dialog.geometry("400x200")
        self.import_dialog.transient(self)
        self.import_dialog.grab_set()

        tki.CTkLabel(self.import_dialog, text="Server Name:").pack(pady=(20, 0))

        suggested_name = os.path.basename(source_dir)
        self.import_name_var = tki.StringVar(value=suggested_name)
        name_entry = tki.CTkEntry(self.import_dialog, width=300, textvariable=self.import_name_var)
        name_entry.pack(pady=10, padx=20)

        # Buttons
        buttons_frame = tki.CTkFrame(self.import_dialog)
        buttons_frame.pack(pady=20)

        tki.CTkButton(buttons_frame, text="Cancel",
                    command=self.import_dialog.destroy).grid(row=0, column=0, padx=10)

        tki.CTkButton(buttons_frame, text="Import",
                    command=lambda: self._do_import_server(source_dir)).grid(row=0, column=1, padx=10)

    def _do_import_server(self, source_dir):  # sourcery skip: low-code-quality
        """Perform the actual server import"""

        try:
            server_name = self.import_name_var.get().strip()

            if not server_name:
                self.show_notification("Please enter a server name", "error")
                return

            # Convert server name to a valid folder name
            folder_name = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in server_name)

            # Create destination directory
            servers_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "servers"))
            dest_dir = os.path.join(servers_dir, folder_name)

            # Check if it already exists
            if os.path.exists(dest_dir):
                counter = 1
                while os.path.exists(f"{dest_dir}_{counter}"):
                    counter += 1
                dest_dir = f"{dest_dir}_{counter}"
                folder_name = f"{folder_name}_{counter}"

            # Create destination directory
            os.makedirs(dest_dir)

            # Copy server files
            for item in os.listdir(source_dir):
                src_item = os.path.join(source_dir, item)
                dst_item = os.path.join(dest_dir, item)

                if os.path.isdir(src_item):
                    shutil.copytree(src_item, dst_item)
                else:
                    shutil.copy2(src_item, dst_item)

            # Create backups directory if it doesn't exist
            backups_dir = os.path.join(dest_dir, "backups")
            if not os.path.exists(backups_dir):
                os.makedirs(backups_dir)

            # Create server_config.json if it doesn't exist
            config_path = os.path.join(dest_dir, "server_config.json")
            if not os.path.exists(config_path):
                # Try to detect server type and version from jar files
                server_type = "unknown"
                version = "unknown"

                for file in os.listdir(dest_dir):
                    if file.endswith(".jar"):
                        lower_name = file.lower()
                        if "paper" in lower_name:
                            server_type = "paper"
                        elif "purpur" in lower_name:
                            server_type = "purpur"
                        elif "spigot" in lower_name:
                            server_type = "spigot"

                        # Try to extract version
                        import re
                        version_match = re.search(r'(\d+\.\d+\.\d+)', file)
                        if version_match:
                            version = version_match.group(1)

                        # Found a server jar, no need to check further
                        break

                # Create default config
                config = {
                    "name": server_name,
                    "description": "Imported Server",
                    "server_type": server_type,
                    "version": version,
                    "memory": "2048",
                    "backup": {
                        "enabled": True,
                        "frequency": 24,
                        "max_backups": 10
                    }
                }

                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)

            # Close the dialog
            self.import_dialog.destroy()

            # Show success message
            self.show_notification(f"Server imported successfully as '{folder_name}'!")

            # Refresh UI
            for widget in self.winfo_children():
                widget.destroy()

            # Re-initialize the main UI
            self.servers = self.get_server_list()
            self.initialize_main_ui()

            # Select the imported server
            if folder_name in self.servers:
                self.server_option.set(folder_name)
                self.change_server(folder_name)

        except Exception as e:
            self.show_notification(f"Import failed: {str(e)}", "error")

    def open_url(self, url):
        """Open a URL in the default browser"""
        import webbrowser
        webbrowser.open(url)

    def get_server_list(self):
        """Get list of server directories"""
        server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers")
        if not os.path.exists(server_dir):
            os.makedirs(server_dir)
        return [d for d in os.listdir(server_dir)
                if os.path.isdir(os.path.join(server_dir, d))]

    # Server management
    def change_server(self, server_name):
        """Change the current selected server"""
        self.current_server = Server(server_name)

        # Add this line to load settings when a server is selected
        self.load_server_settings()
        self.load_optimization_settings()

        if '-nogui' in self.args:
            return
        try:
            self.update_dashboard()
            self.update_console()
            self.update_players()
            self.update_plugins()

        except Exception as e:
            raise e
            self.show_notification(f"Error changing server: {str(e)}", "error")

    def start_server(self):
        """Start the selected server with conflict detection"""
        if not self.current_server:
            return

        # Check if server port is already in use
        port = int(self.current_server.get_port())
        if self.is_port_in_use(port):
            if '-nogui' in self.args:
                exit(f"Port {port} is already in use. Please check for other running servers.")
            self.show_notification(
                f"Port {port} is already in use. Please check for other running servers.",
                "warning"
            )
            return

        # Now safe to start the server
        self.current_server.start()

        if '-nogui' in self.args:
            return

        try:
            self.update_dashboard()
        except Exception:
            ...

    def stop_server(self):
        """Stop the selected server"""
        if self.current_server:
            self.current_server.stop()
            self.update_dashboard()

    def restart_server(self):
        """Restart the selected server"""
        if self.current_server:
            self.current_server.restart()
            self.update_dashboard()

    def load_server_settings(self):    # sourcery skip: low-code-quality
        """Load server settings from config file"""
        if not hasattr(self, 'current_server') or not self.current_server:
            return

        server_dir = self.current_server.base_dir
        config_file = os.path.join(server_dir, "server_config.json")
        props_file = os.path.join(server_dir, "server.properties")

        # Default settings
        self.settings = {
            "general": {
                "server-name": self.current_server.name,
                "motd": "A Minecraft Server",
                "server-port": "25565",
                "max-players": 20,
                "gamemode": "survival",
                "difficulty": "normal"
            },
            "world": {
                "level-name": "world",
                "level-type": "default",
                "pvp": True
            },
            "advanced": {
                "memory": 4096, # MB
                "view-distance": 10,
                "online-mode": True
            }
        }

        # Load from file if it exists
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    saved_settings = json.load(f)

                    # Update settings with saved values
                    for category in ["general", "world", "advanced"]:
                        if category in saved_settings:
                            self.settings[category].update(saved_settings[category])

                # Also read from server.properties for additional settings
                if os.path.exists(props_file):
                    with open(props_file, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith('#'):
                                try:
                                    key, value = line.split('=', 1)
                                    # Map to the right category
                                    if key in ['gamemode', 'difficulty', 'motd', 'server-port', 'max-players']:
                                        self.settings['general'][key] = value
                                    elif key in ['level-seed', 'level-type', 'generate-structures',
                                                 'allow-nether', 'spawn-npcs', 'spawn-animals', 'spawn-monsters']:
                                        # Convert string boolean values to actual boolean
                                        if value.lower() == 'true':
                                            self.settings['world'][key] = True
                                        elif value.lower() == 'false':
                                            self.settings['world'][key] = False
                                        else:
                                            self.settings['world'][key] = value
                                    elif key in ['online-mode', 'enable-command-block', 'pvp',
                                                'force-gamemode', 'allow-flight', 'view-distance']:
                                        if value.lower() == 'true':
                                            self.settings['advanced'][key] = True
                                        elif value.lower() == 'false':
                                            self.settings['advanced'][key] = False
                                        else:
                                            self.settings['advanced'][key] = value
                                except Exception:
                                    pass

                # Update UI with loaded settings
                if '-nogui' not in self.args and hasattr(self, 'current_server') and self.current_server and self.current_server._is_running:
                    self.apply_settings_to_ui()

                if self.current_server:
                    self.current_server.max_ram = self.settings['advanced']['memory']

            except Exception as e:
                print(f"Error loading settings: {e}")
                traceback.print_exc()

        else:
            print('Config file not found, using default settings')

    def apply_settings_to_ui(self):  # sourcery skip: low-code-quality
        """Apply loaded settings to UI widgets with debug output"""
        # Check if gamemode/difficulty are in the right categories
        general_settings = self.settings.get("general", {})
        world_settings = self.settings.get("world", {})
        advanced_settings = self.settings.get("advanced", {})

        # Move gamemode and difficulty to general if they're in world settings
        if "gamemode" in world_settings:
            general_settings["gamemode"] = world_settings.pop("gamemode")
        if "difficulty" in world_settings:
            general_settings["difficulty"] = world_settings.pop("difficulty")

        # Update General tab widgets
        for key, value in general_settings.items():
            widget_name = f"setting_{key}"
            if hasattr(self, widget_name):
                widget = getattr(self, widget_name)

                try:
                    # Handle different widget types
                    if isinstance(widget, tki.CTkComboBox):  # Dropdown
                        widget.set(str(value))
                    elif isinstance(widget, tki.CTkSwitch):  # Switch
                        if value:
                            widget.select()
                        else:
                            widget.deselect()
                    elif isinstance(widget, tki.CTkEntry):  # Text entry
                        widget.delete(0, tki.END)
                        widget.insert(0, str(value))
                except Exception as e:
                    print(f"Error setting {widget_name}: {e}")

        # Update World tab widgets
        for key, value in world_settings.items():
            widget_name = f"setting_{key}"
            if hasattr(self, widget_name):
                widget = getattr(self, widget_name)

                try:
                    # Handle different widget types
                    if isinstance(widget, tki.CTkComboBox):
                        widget.set(str(value))
                    elif isinstance(widget, tki.CTkSwitch):
                        if value:
                            widget.select()
                        else:
                            widget.deselect()
                    elif isinstance(widget, tki.CTkEntry):
                        widget.delete(0, tki.END)
                        widget.insert(0, str(value))
                except Exception as e:
                    print(f"Error setting {widget_name}: {e}")

        # Update Advanced tab widgets
        for key, value in advanced_settings.items():
            widget_name = f"setting_{key}"
            if hasattr(self, widget_name):
                widget = getattr(self, widget_name)

                try:
                    # Handle different widget types
                    if isinstance(widget, tki.CTkComboBox):
                        widget.set(str(value))
                    elif isinstance(widget, tki.CTkSwitch):
                        if value:
                            widget.select()
                        else:
                            widget.deselect()
                    elif isinstance(widget, tki.CTkEntry):
                        widget.delete(0, tki.END)
                        widget.insert(0, str(value))
                except Exception as e:
                    print(f"Error setting {widget_name}: {e}")

    def is_port_in_use(self, port):
        """Check if a port is already in use"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    def start_tunnel(self):
        """Start an ngrok tunnel for the current server port"""
        if not self.current_server:
            self.show_notification("No server selected", "error")
            return

        # Get the server port
        server_port = self.current_server.get_port()
        if not server_port:
            self.show_notification("Could not determine server port", "error")
            return

        # Check if ngrok is already running
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            if 'ngrok' in proc.info['name'].lower():
                self.show_notification("Ngrok is already running", "warning")
                return

        # Create progress window
        self.tunnel_progress = tki.CTkToplevel(self)
        self.tunnel_progress.title("Setting up tunnel")
        self.tunnel_progress.geometry("500x300")
        self.tunnel_progress.transient(self)
        self.tunnel_progress.grab_set()

        # Configure grid
        self.tunnel_progress.grid_columnconfigure(0, weight=1)

        # Title
        title = tki.CTkLabel(self.tunnel_progress, text="Setting up External Tunnel",
                        font=tki.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Status label
        self.tunnel_status = tki.CTkLabel(self.tunnel_progress, text="Checking for ngrok...")
        self.tunnel_status.grid(row=1, column=0, padx=20, pady=(0, 10))

        # Progress bar
        self.tunnel_progress_bar = tki.CTkProgressBar(self.tunnel_progress, width=400)
        self.tunnel_progress_bar.grid(row=2, column=0, padx=20, pady=(0, 10))
        self.tunnel_progress_bar.set(0.1)

        # Detail log
        log_frame = tki.CTkFrame(self.tunnel_progress)
        log_frame.grid(row=3, column=0, padx=20, pady=10, sticky="nsew")
        self.tunnel_progress.grid_rowconfigure(3, weight=1)

        self.tunnel_log = tki.CTkTextbox(log_frame, height=120, width=400)
        self.tunnel_log.pack(fill=tki.BOTH, expand=True, padx=10, pady=10)

        # Start tunnel setup in a separate thread
        threading.Thread(target=self._setup_tunnel_thread, args=(server_port,), daemon=True).start()

    def _setup_tunnel_thread(self, port):
        """Thread to handle ngrok setup and tunnel creation"""
        try:
            # Add log entry
            self._add_tunnel_log("Starting tunnel setup...")

            # Determine ngrok path
            ngrok_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
            os.makedirs(ngrok_dir, exist_ok=True)

            ngrok_exe = os.path.join(ngrok_dir, "ngrok.exe")

            # Check if ngrok exists
            if not os.path.exists(ngrok_exe):
                self._update_tunnel_status("Downloading ngrok...", 0.2)
                # Download code...

            # Start ngrok
            self._update_tunnel_status("Starting tunnel...", 0.7)
            self._add_tunnel_log(f"Starting ngrok TCP tunnel on port {port}")

            # Run ngrok with output capture
            self.ngrok_process = subprocess.Popen(
                [ngrok_exe, "tcp", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            # Wait longer for ngrok to start
            self._update_tunnel_status("Waiting for tunnel to initialize...", 0.8)
            time.sleep(2)  # Increased wait time

            # Check if process is still running
            if self.ngrok_process.poll() is not None:
                # Process exited - get error message
                _, stderr = self.ngrok_process.communicate()
                self._add_tunnel_log(f"Ngrok failed to start: {stderr}")
                self._update_tunnel_status("Failed to start tunnel", 0)
                self.after(0, lambda: self.show_notification(f"Tunnel creation failed: ngrok process exited", "error"))
                return

            # Try to get URL from API with retries
            self._update_tunnel_status("Retrieving tunnel address...", 0.9)

            # Attempt to get tunnel info from API with retries
            tunnel_url = None
            max_retries = 15

            for retry in range(max_retries):
                try:
                    self._add_tunnel_log(f"Connecting to ngrok API (attempt {retry+1}/{max_retries})...")
                    response = requests.get("http://localhost:4040/api/tunnels", timeout=3)

                    if response.status_code == 200:
                        data = response.json()
                        if "tunnels" in data and data["tunnels"]:
                            tunnel = data["tunnels"][0]
                            tunnel_url = tunnel["public_url"]
                            break
                        else:
                            self._add_tunnel_log("No tunnels found in API response")
                    else:
                        self._add_tunnel_log(f"API returned status code: {response.status_code}")

                except Exception as e:
                    self._add_tunnel_log(f"API connection attempt {retry+1} failed: {str(e)}")

                # Wait before retrying
                time.sleep(1.5)

            # Process the tunnel URL if found
            if tunnel_url:
                # Extract host and port from URL (tcp://0.tcp.ngrok.io:12345)

                match = re.search(r'tcp://([^:]+):(\d+)', tunnel_url)
                if match:
                    host, tunnel_port = match.groups()

                    # Use our robust resolution method
                    ip_address = _resolve_hostname(host)

                    if ip_address:
                        # Create connection messages with both hostname and IP
                        message = (f"Tunnel created!\n\n"
                                f"IP Address: {ip_address}\n"  # Show IP first for emphasis
                                f"Hostname: {host} (may not work with all DNS providers)\n"
                                f"Port: {tunnel_port}\n\n"
                                f"Players should connect using:\n"
                                f"{ip_address}:{tunnel_port}")

                        self._update_tunnel_status("Tunnel created successfully!", 1.0)
                        self._add_tunnel_log(message)

                        # Show in message box
                        self.after(0, lambda: self.show_notification(
                            f"Tunnel created!\n\n"
                            f"IP Address: {ip_address}\n"
                            f"Hostname: {host}\n"
                            f"Port: {tunnel_port}\n\n"
                            f"Players should connect using:\n"
                            f"{ip_address}:{tunnel_port}"
                        ))
                    else:
                        # If resolution fails completely
                        self._add_tunnel_log(f"Could not resolve hostname {host} to IP address")
                        message = f"Tunnel created!\nHostname: {host}\nPort: {tunnel_port}\n\nTry connecting using: {host}:{tunnel_port}"

        except Exception as e:
            self._add_tunnel_log(f"Error: {str(e)}")
            self._update_tunnel_status(f"Error: {str(e)}", 0)
            self.after(0, self.show_notification, f"Tunnel creation failed: {str(e)}", "error")

    def _extract_tunnel_info_from_output(self, process):
        """Extract tunnel address from ngrok output and resolve IP"""
        try:
            import re
            import socket

            # Read a limited amount of output
            output = ""
            start_time = time.time()

            # Try reading output for up to 5 seconds
            while time.time() - start_time < 5:
                if process.stdout.readable():
                    line = process.stdout.readline()
                    if not line:
                        time.sleep(0.1)
                        continue

                    output += line
                    self._add_tunnel_log(f"Ngrok output: {line.strip()}")

                    # Look for tunnel URL in the output
                    # Format might be like: "Forwarding tcp://0.tcp.ngrok.io:12345 -> localhost:25565"
                    match = re.search(r'tcp://([^:]+):(\d+)', line)
                    if match:
                        host, port = match.groups()

                        # Try to resolve hostname to IP
                        try:
                            ip_address = socket.gethostbyname(host)
                            self._add_tunnel_log(f"Resolved hostname {host} to IP: {ip_address}")

                            # Create message with both hostname and IP
                            message = (f"Tunnel created!\n\n"
                                      f"Hostname: {host}\n"
                                      f"IP Address: {ip_address}\n"
                                      f"Port: {port}\n\n"
                                      f"Players can connect using either:\n"
                                      f"{host}:{port}\n"
                                      f"or\n"
                                      f"{ip_address}:{port}")

                            self._update_tunnel_status("Tunnel created successfully!", 1.0)
                            self._add_tunnel_log(message)

                            # Show in a message box
                            self.after(0, lambda: self.show_notification(
                                f"Tunnel created!\n\n"
                                f"Hostname: {host}\n"
                                f"IP Address: {ip_address}\n"
                                f"Port: {port}\n\n"
                                f"Players can connect using either:\n"
                                f"{host}:{port}\n"
                                f"or\n"
                                f"{ip_address}:{port}"
                            ))
                            return True
                        except socket.gaierror:
                            # If hostname can't be resolved, still show what we have
                            self._add_tunnel_log(f"Could not resolve hostname {host} to IP address")
                            message = f"Tunnel created!\nAddress: {host}\nPort: {port}\n\nPlayers can connect using: {host}:{port}"
                            self._update_tunnel_status("Tunnel created successfully!", 1.0)
                            self._add_tunnel_log(message)

                            # Show in a message box
                            self.after(0, lambda: self.show_notification(
                                f"Tunnel created!\n\nAddress: {host}:{port}\n\nShare this address with players to connect."
                            ))
                            return True
                else:
                    time.sleep(0.1)

            # If we get here, couldn't find the tunnel URL in the output
            self._add_tunnel_log("Tunnel appears to be running, but couldn't determine the address.")
            self._add_tunnel_log("Try checking the ngrok web interface at http://localhost:4040")
            self._update_tunnel_status("Open http://localhost:4040 in your browser to see tunnel details", 1.0)
            self.after(0, lambda: self.show_notification(
                "Tunnel created, but couldn't extract address automatically.\n\nOpen http://localhost:4040 in your browser to see connection details."
            ))
            return False

        except Exception as e:
            self._add_tunnel_log(f"Error extracting tunnel info: {str(e)}")
            return False

    def _update_tunnel_status(self, message, progress_value):
        """Update tunnel progress status"""
        def update_ui():
            self.tunnel_status.configure(text=message)
            self.tunnel_progress_bar.set(progress_value)

        self.after(0, update_ui)

    def _add_tunnel_log(self, message):
        """Add message to tunnel log"""
        def update_log():
            self.tunnel_log.configure(state="normal")
            self.tunnel_log.insert("end", f"{message}\n")
            self.tunnel_log.configure(state="disabled")
            self.tunnel_log.see("end")

        self.after(0, update_log)

    def close_tunnel(self):
        if not hasattr(self, 'ngrok_tunnel'):
            return

        self.ngrok_tunnel.terminate()

    # Setup tabs --- MARKER ---
    def setup_dashboard_tab(self):
        """Setup the dashboard tab with resource usage graphs - MORE COMPACT"""
        # Status frame - REDUCED PADDING
        self.status_frame = tki.CTkFrame(self.dashboard_tab)
        self.status_frame.grid(
            row=0, column=0, padx=10, pady=10, sticky="new"  # REDUCED PADDING
        )

        self.status_label = tki.CTkLabel(
            self.status_frame,
            text="Status:",
            font=tki.CTkFont(weight="bold")
        )
        self.status_label.grid(
            row=0, column=0, padx=5, pady=5, sticky="w"  # REDUCED PADDING
        )

        self.status_value = tki.CTkLabel(
            self.status_frame,
            text="Stopped",
            text_color="red"
        )
        self.status_value.grid(
            row=0, column=1, padx=5, pady=5, sticky="w"  # REDUCED PADDING
        )

        self.version_label = tki.CTkLabel(
            self.status_frame,
            text="Version:",
            font=tki.CTkFont(weight="bold")
        )
        self.version_label.grid(
            row=1, column=0, padx=5, pady=5, sticky="w"  # REDUCED PADDING
        )

        self.version_value = tki.CTkLabel(
            self.status_frame,
            text="Unknown"
        )
        self.version_value.grid(
            row=1, column=1, padx=5, pady=5, sticky="w"  # REDUCED PADDING
        )

        # Create info frame for uptime and player count
        self.info_frame = tki.CTkFrame(self.dashboard_tab)
        self.info_frame.grid(
            row=2, column=0, padx=10, pady=(0, 10), sticky="new"
        )

        # Resource usage frames - REDUCED PADDING, SMALLER CHARTS
        self.resources_frame = tki.CTkFrame(self.dashboard_tab)
        self.resources_frame.grid(
            row=1,
            column=0,
            padx=10,
            pady=(0, 10),
            sticky="nsew"  # REDUCED PADDING
        )
        self.resources_frame.grid_columnconfigure(0, weight=1)
        self.resources_frame.grid_columnconfigure(1, weight=1)

        # Get the background color from the app theme
        bg_color = "#2b2b2b"  # Dark theme background
        grid_color = "#3f3f3f"
        text_color = "#dcddde"

        # CPU Usage Chart - SMALLER SIZE
        self.cpu_frame = tki.CTkFrame(self.resources_frame)
        self.cpu_frame.grid(
            row=0,
            column=0,
            padx=5,
            pady=5,
            sticky="nsew"  # REDUCED PADDING
        )

        # CPU Header Frame (for title and value)
        cpu_header = tki.CTkFrame(
            self.cpu_frame,
            fg_color="transparent"
        )
        cpu_header.pack(fill="x", pady=(5, 0))

        self.cpu_label = tki.CTkLabel(
            cpu_header,
            text="CPU Usage",
            font=tki.CTkFont(weight="bold")
        )
        self.cpu_label.pack(side="left", padx=(5, 0))

        # Create value label that will be updated with current CPU usage
        self.cpu_value_label = tki.CTkLabel(
            cpu_header,
            text="0.0%",
            text_color="blue"
        )
        self.cpu_value_label.pack(side="right", padx=(0, 5))

        # Create matplotlib figure for CPU - SMALLER SIZE
        self.cpu_fig, self.cpu_ax = plt.subplots(figsize=(3, 2))  # SMALLER FIGURE
        self.cpu_canvas = FigureCanvasTkAgg(
            self.cpu_fig,
            master=self.cpu_frame
        )
        self.cpu_canvas.get_tk_widget().pack(
            fill=tki.BOTH,
            expand=True,
            padx=5,
            pady=5  # REDUCED PADDING
        )

        # Style the CPU plot
        self.cpu_fig.patch.set_facecolor(bg_color)
        self.cpu_ax.set_facecolor(bg_color)
        self.cpu_ax.tick_params(
            axis='both',
            colors=text_color,
            labelsize=7
        )
        self.cpu_ax.xaxis.label.set_color(text_color)
        self.cpu_ax.yaxis.label.set_color(text_color)
        self.cpu_ax.spines['bottom'].set_color(grid_color)
        self.cpu_ax.spines['top'].set_color(grid_color)
        self.cpu_ax.spines['left'].set_color(grid_color)
        self.cpu_ax.spines['right'].set_color(grid_color)

        self.cpu_data = []
        self.cpu_times = []
        self.cpu_line, = self.cpu_ax.plot(
            [],
            [],
            'b-',
            linewidth=1.5
        )

        self.cpu_ax.set_ylim(0, 100)
        self.uptime_value = tki.CTkLabel(self.info_frame, text="00:00:00")
        self.uptime_value.grid(row=0, column=1, padx=5, pady=5, sticky="w")  # REDUCED PADDING

        self.players_label = tki.CTkLabel(self.info_frame, text="Players:", font=tki.CTkFont(weight="bold"))
        self.players_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")  # REDUCED PADDING

        self.players_value = tki.CTkLabel(self.info_frame, text="0/0")
        self.players_value.grid(row=1, column=1, padx=5, pady=5, sticky="w")  # REDUCED PADDING

        # RAM Usage Chart - Add this section
        self.ram_frame = tki.CTkFrame(self.resources_frame)
        self.ram_frame.grid(
            row=0,
            column=1,
            padx=5,
            pady=5,
            sticky="nsew"  # REDUCED PADDING
        )

        # RAM Header Frame (for title and value)
        ram_header = tki.CTkFrame(
            self.ram_frame,
            fg_color="transparent"
        )
        ram_header.pack(fill="x", pady=(5, 0))

        self.ram_label = tki.CTkLabel(
            ram_header,
            text="RAM Usage",
            font=tki.CTkFont(weight="bold")
        )
        self.ram_label.pack(side="left", padx=(5, 0))

        # Create value label that will be updated with current RAM usage
        self.ram_value_label = tki.CTkLabel(
            ram_header,
            text="0.0% (0.0 GB)",
            text_color="green"
        )
        self.ram_value_label.pack(side="right", padx=(0, 5))

        # Create matplotlib figure for RAM - SMALLER SIZE
        self.ram_fig, self.ram_ax = plt.subplots(figsize=(3, 2))  # SMALLER FIGURE
        self.ram_canvas = FigureCanvasTkAgg(
            self.ram_fig,
            master=self.ram_frame
        )
        self.ram_canvas.get_tk_widget().pack(
            fill=tki.BOTH,
            expand=True,
            padx=5,
            pady=5  # REDUCED PADDING
        )

        # Style the RAM plot
        self.ram_fig.patch.set_facecolor(bg_color)
        self.ram_ax.set_facecolor(bg_color)
        self.ram_ax.tick_params(
            axis='both',
            colors=text_color,
            labelsize=7
        )
        self.ram_ax.xaxis.label.set_color(text_color)
        self.ram_ax.yaxis.label.set_color(text_color)
        self.ram_ax.spines['bottom'].set_color(grid_color)
        self.ram_ax.spines['top'].set_color(grid_color)
        self.ram_ax.spines['left'].set_color(grid_color)
        self.ram_ax.spines['right'].set_color(grid_color)

        self.ram_data = []
        self.ram_times = []
        self.ram_line, = self.ram_ax.plot(
            [],
            [],
            'g-',
            linewidth=1.5
        )

        self.ram_ax.set_ylim(0, 110)

        # Add the missing uptime label and player count
        self.uptime_label = tki.CTkLabel(self.info_frame, text="Uptime:", font=tki.CTkFont(weight="bold"))
        self.uptime_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.uptime_value = tki.CTkLabel(self.info_frame, text="00:00:00")
        self.uptime_value.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        self.players_label = tki.CTkLabel(self.info_frame, text="Players:", font=tki.CTkFont(weight="bold"))
        self.players_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        self.players_value = tki.CTkLabel(self.info_frame, text="0/0")
        self.players_value.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    def setup_console_tab(self):
        """Setup the console tab with live server console output"""
        # Create console output area
        self.console_output = tki.CTkTextbox(self.console_tab, wrap="word")
        self.console_output.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))

        # Create input frame
        self.console_input_frame = tki.CTkFrame(self.console_tab)
        self.console_input_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.console_input_frame.grid_columnconfigure(0, weight=1)

        # Command input
        self.command_var = tki.StringVar()
        self.console_input = tki.CTkEntry(self.console_input_frame, textvariable=self.command_var)
        self.console_input.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        # Send button
        self.send_button = tki.CTkButton(
            self.console_input_frame,
            text="Send",
            command=self.send_command,
            width=80
        )

        self.send_button.grid(row=0, column=1)

        # Bind Enter key to send command
        self.console_input.bind("<Return>", lambda event: self.send_command())

        # Start periodic console update
        self.after(1000, self.update_console)

    def setup_plugins_tab(self):
        """Setup the plugins tab with plugin management"""
        # Plugins frame
        self.plugins_frame = tki.CTkFrame(self.plugins_tab)
        self.plugins_frame.pack(fill=tki.BOTH, expand=True, padx=20, pady=20)
        self.plugins_frame.grid_columnconfigure(0, weight=1)

        # Header
        self.plugins_header = tki.CTkFrame(self.plugins_frame)
        self.plugins_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))

        self.plugins_title = tki.CTkLabel(
            self.plugins_header,
            text="Installed Plugins",
            font=tki.CTkFont(size=16, weight="bold")
        )
        self.plugins_title.pack(side=tki.LEFT, padx=5, pady=5)

        self.reload_plugins_btn = tki.CTkButton(
            self.plugins_header,
            text="Reload Plugins",
            command=self.reload_plugins
        )
        self.reload_plugins_btn.pack(side=tki.RIGHT, padx=5, pady=5)

        self.install_plugin_btn = tki.CTkButton(
            self.plugins_header,
            text="Install New Plugin",
            command=self.install_plugin
        )
        self.install_plugin_btn.pack(side=tki.RIGHT, padx=5, pady=5)

        # Plugins list frame
        self.plugins_list_frame = tki.CTkScrollableFrame(self.plugins_frame)
        self.plugins_list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        self.plugins_frame.grid_rowconfigure(1, weight=1)

        # Will be populated by update_plugins method
        self.plugin_frames = []

    def setup_players_tab(self):
        """Setup the players tab with online player list and controls"""
        # Create players frame
        self.players_frame = tki.CTkFrame(self.players_tab)
        self.players_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=10,
            pady=10
        )
        self.players_frame.grid_columnconfigure(0, weight=1)

        # Player count header
        self.player_count_label = tki.CTkLabel(
            self.players_frame,
            text="Online Players: 0/0",
            font=tki.CTkFont(size=16, weight="bold")
        )
        self.player_count_label.grid(
            row=0,
            column=0,
            padx=10,
            pady=10,
            sticky="w"
        )

        # Refresh button
        self.refresh_players_btn = tki.CTkButton(
            self.players_frame,
            text="Refresh",
            command=self.update_players_list
        )
        self.refresh_players_btn.grid(
            row=0,
            column=1,
            padx=10,
            pady=10
        )

        # Player list
        self.players_list = tki.CTkScrollableFrame(self.players_frame)
        self.players_list.grid(
            row=1,
            column=0,
            columnspan=2,
            padx=10,
            pady=10,
            sticky="nsew"
        )

        # Start periodic player list update
        self.after(1000, self.update_players_list)

    def update_players_list(self):
        """Update the list of online players"""
        if hasattr(self, 'current_server') and self.current_server:
            # Update player count label
            players = self.current_server.get_players()
            max_players = self.current_server.get_max_players()
            self.player_count_label.configure(text=f"Online Players: {len(players)}/{max_players}")

            # Use the existing update_players method which properly creates all UI elements
            self.update_players()

        # Schedule next update
        self.after(1000, self.update_players_list)

    def setup_backups_tab(self):
        """Setup the backups tab with backup management"""
        # Backups frame
        self.backups_frame = tki.CTkFrame(self.backups_tab)
        self.backups_frame.pack(
            fill=tki.BOTH,
            expand=True,
            padx=20,
            pady=20
        )
        self.backups_frame.grid_columnconfigure(0, weight=1)

        # Header
        self.backups_header = tki.CTkFrame(self.backups_frame)
        self.backups_header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(10, 0)
        )

        self.backups_title = tki.CTkLabel(
            self.backups_header,
            text="Server Backups",
            font=tki.CTkFont(size=16, weight="bold")
        )
        self.backups_title.pack(side=tki.LEFT, padx=5, pady=5)

        self.create_backup_btn = tki.CTkButton(
            self.backups_header,
            text="Create Backup",
            command=self.create_backup
        )
        self.create_backup_btn.pack(side=tki.RIGHT, padx=5, pady=5)

        self.refresh_backups_btn = tki.CTkButton(
            self.backups_header,
            text="Refresh",
            command=self.update_backups
        )
        self.refresh_backups_btn.pack(side=tki.RIGHT, padx=5, pady=5)

        # Backups list frame
        self.backups_list_frame = tki.CTkScrollableFrame(self.backups_frame)
        self.backups_list_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=10
        )
        self.backups_frame.grid_rowconfigure(1, weight=1)

        # Backup schedule frame
        self.backup_schedule_frame = tki.CTkFrame(self.backups_frame)
        self.backup_schedule_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=10,
            pady=10
        )

        self.after_label = tki.CTkLabel(
            self.backup_schedule_frame,
            text="Automatic Backups:",
            font=tki.CTkFont(weight="bold")
        )
        self.after_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.after_enabled = tki.CTkSwitch(
            self.backup_schedule_frame,
            text="Enabled"
        )
        self.after_enabled.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        self.after_interval_label = tki.CTkLabel(
            self.backup_schedule_frame,
            text="Interval (hours):"
        )
        self.after_interval_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")

        self.after_interval = tki.CTkComboBox(
            self.backup_schedule_frame,
            values=["1", "3", "6", "12", "24"]
        )
        self.after_interval.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        self.after_interval.set("24")

        self.max_backups_label = tki.CTkLabel(
            self.backup_schedule_frame,
            text="Max backups to keep:"
        )
        self.max_backups_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")

        self.max_backups = tki.CTkComboBox(
            self.backup_schedule_frame,
            values=["5", "10", "20", "30", "All"]
        )
        self.max_backups.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        self.max_backups.set("10")

        self.save_schedule_btn = tki.CTkButton(
            self.backup_schedule_frame,
            text="Save Schedule",
            command=self.save_backup_schedule
        )
        self.save_schedule_btn.grid(
            row=3,
            column=0,
            columnspan=2,
            padx=10,
            pady=10,
            sticky="ew"
        )

    def setup_settings_tab(self):  # sourcery skip: low-code-quality
        """Setup the settings tab with server configuration options"""
        # Create settings frame
        self.settings_frame = tki.CTkFrame(self.settings_tab)
        self.settings_frame.grid(
            row=0, column=0, sticky="nsew", padx=10, pady=10, columnspan=2
        )

        # Server properties section
        self.server_props_label = tki.CTkLabel(
            self.settings_frame,
            text="Server Properties",
            font=tki.CTkFont(size=16, weight="bold")
        )
        self.server_props_label.grid(
            row=0, column=0, padx=10, pady=(10, 5), sticky="nsew"
        )

        self.settings_tabview = tki.CTkTabview(self.settings_frame)
        self.settings_tabview.grid(
            row=1, column=0, sticky="nsew", padx=10, pady=10
        )
        self.settings_frame.grid_rowconfigure(1, weight=1)

        # Create settings tabs
        self.general_tab = self.settings_tabview.add("General")
        self.world_tab = self.settings_tabview.add("World")
        self.advanced_tab = self.settings_tabview.add("Advanced")

        # General settings
        self.general_frame = tki.CTkScrollableFrame(self.general_tab, width=400)
        self.general_frame.pack(fill=tki.BOTH, expand=True, padx=5, pady=5)

        settings = [
            ("Server Name", "server-name", "Minecraft Server"),
            ("MOTD", "motd", "A Minecraft Server"),
            ("Server Port", "server-port", "25565"),
            ("Max Players", "max-players", "20"),
            ("View Distance", "view-distance", "10"),
            ("Gamemode", "gamemode", "survival"),
            ("Difficulty", "difficulty", "easy")
        ]

        for i, (label, prop, default) in enumerate(settings):
            lbl = tki.CTkLabel(self.general_frame, text=f"{label}:", anchor="w")
            lbl.grid(row=i, column=0, padx=10, pady=5, sticky="w")

            if prop == "gamemode":
                widget = tki.CTkComboBox(
                    self.general_frame,
                    values=["survival", "creative", "adventure", "spectator"]
                )
                widget.set(default)
            elif prop == "difficulty":
                widget = tki.CTkComboBox(
                    self.general_frame,
                    values=["peaceful", "easy", "normal", "hard"]
                )
                widget.set(default)
            else:
                widget = tki.CTkEntry(self.general_frame)
                widget.insert(0, default)

            # Grid and attribute setting moved outside the if-else block
            widget.grid(row=i, column=1, padx=10, pady=5, sticky="ew")
            setattr(self, f"setting_{prop}", widget)

        # World settings
        self.world_frame = tki.CTkScrollableFrame(self.world_tab, width=400)
        self.world_frame.pack(fill=tki.BOTH, expand=True, padx=5, pady=5)

        world_settings = [
            ("World Seed", "level-seed", ""),
            ("Level Type", "level-type", "default"),
            ("Generate Structures", "generate-structures", True),
            ("Allow Nether", "allow-nether", True),
            ("Spawn NPCs", "spawn-npcs", True),
            ("Spawn Animals", "spawn-animals", True),
            ("Spawn Monsters", "spawn-monsters", True)
        ]

        for i, (label, prop, default) in enumerate(world_settings):
            lbl = tki.CTkLabel(self.world_frame, text=f"{label}:", anchor="w")
            lbl.grid(row=i, column=0, padx=10, pady=5, sticky="w")

            if isinstance(default, bool):
                widget = tki.CTkSwitch(self.world_frame, text="")
                if default:
                    widget.select()
            elif prop == "level-type":
                widget = tki.CTkComboBox(
                    self.world_frame,
                    values=["default", "flat", "largeBiomes", "amplified"]
                )
                widget.set(default)
            else:
                widget = tki.CTkEntry(self.world_frame)
                widget.insert(0, default)

            widget.grid(row=i, column=1, padx=10, pady=5, sticky="ew")
            setattr(self, f"setting_{prop}", widget)

        # Advanced settings
        self.advanced_frame = tki.CTkScrollableFrame(self.advanced_tab, width=400)
        self.advanced_frame.pack(fill=tki.BOTH, expand=True, padx=5, pady=5)

        advanced_settings = [
            ("Java Memory (MB)", "memory", "4096"),
            ("Enable Command Blocks", "enable-command-block", False),
            ("PVP Enabled", "pvp", True),
            ("Force Gamemode", "force-gamemode", False),
            ("Allow Flight", "allow-flight", False)
        ]

        for i, (label, prop, default) in enumerate(advanced_settings):
            lbl = tki.CTkLabel(self.advanced_frame, text=f"{label}:", anchor="w")
            lbl.grid(row=i, column=0, padx=10, pady=5, sticky="w")

            if isinstance(default, bool):
                widget = tki.CTkSwitch(self.advanced_frame, text="")
                if default:
                    widget.select()
            else:
                widget = tki.CTkEntry(self.advanced_frame)
                widget.insert(0, default)

            widget.grid(row=i, column=1, padx=10, pady=5, sticky="ew")
            setattr(self, f"setting_{prop}", widget)

        # Save button
        self.save_settings_btn = tki.CTkButton(
            self.settings_frame,
            text="Save Settings",
            command=self.save_settings
        )
        self.save_settings_btn.grid(
            row=2, column=0, padx=10, pady=(10, 20), sticky="ew"
        )

    def setup_optimizations_tab(self):
        """Setup the optimizations tab with server performance tweaks"""
        self.optimizations_frame = tki.CTkFrame(self.optimizations_tab)
        self.optimizations_frame.grid(
            row=0, column=0, sticky="nsew", padx=10, pady=10, columnspan=2
        )
        self.optimizations_frame.grid_rowconfigure(1, weight=1)
        self.optimizations_frame.grid_columnconfigure(0, weight=1)

        # Warning header
        self.optimizations_warning = tki.CTkLabel(
            self.optimizations_frame,
            text='WARNING: These settings may cause unintended side-effects!\nBack up your configurations before you apply them!',
            font=tki.CTkFont(size=16, weight="bold"),
            fg_color='#ff0000',
            corner_radius=5,
            text_color="white"
        )
        self.optimizations_warning.grid(row=0, column=0, sticky="new", padx=10, pady=10)

        # Create scrollable frame for optimization settings
        self.optimizations_list = tki.CTkScrollableFrame(self.optimizations_frame)
        self.optimizations_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        # Settings section heading
        vanilla_heading = tki.CTkLabel(
            self.optimizations_list,
            text="Vanilla Settings",
            font=tki.CTkFont(size=14, weight="bold")
        )
        vanilla_heading.grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10))

        # Vanilla settings
        settings_vanilla = [
            ("Simulation Distance", "simulation-distance", 10),
            ("Entity Broadcast Range", "entity-broadcast-range-percentage", 100)
        ]

        # Add vanilla settings UI
        for i, (label, key, default) in enumerate(settings_vanilla):
            setting_label = tki.CTkLabel(self.optimizations_list, text=label)
            setting_label.grid(row=i+1, column=0, padx=10, pady=5, sticky="w")

            setting_var = tki.StringVar(value=str(default))
            setting_entry = tki.CTkEntry(self.optimizations_list, width=100, textvariable=setting_var)
            setting_entry.grid(row=i+1, column=1, padx=10, pady=5, sticky="w")

            # Store reference to the widget using the key
            setattr(self, f"opt_vanilla_{key.replace('-', '_')}", setting_entry)

        # Bukkit settings section heading
        bukkit_heading = tki.CTkLabel(
            self.optimizations_list,
            text="Bukkit Settings",
            font=tki.CTkFont(size=14, weight="bold")
        )
        bukkit_heading.grid(row=len(settings_vanilla)+1, column=0, columnspan=2, sticky="w", padx=10, pady=(20, 10))

        # Bukkit settings
        settings_bukkit = [
            ("Chunk GC Ticks", "period-in-ticks", 600),
            ("Mob Cap (monsters)", "monsters", 70),
        ]

        # Add bukkit settings UI
        for i, (label, key, default) in enumerate(settings_bukkit):
            row = len(settings_vanilla) + i + 2
            setting_label = tki.CTkLabel(self.optimizations_list, text=label)
            setting_label.grid(row=row, column=0, padx=10, pady=5, sticky="w")

            setting_var = tki.StringVar(value=str(default))
            setting_entry = tki.CTkEntry(self.optimizations_list, width=100, textvariable=setting_var)
            setting_entry.grid(row=row, column=1, padx=10, pady=5, sticky="w")

            # Store reference to the widget using the key
            setattr(self, f"opt_bukkit_{key.replace('-', '_')}", setting_entry)

        # Optimization presets section heading
        presets_heading = tki.CTkLabel(
            self.optimizations_list,
            text="Optimization Presets",
            font=tki.CTkFont(size=14, weight="bold")
        )
        presets_heading.grid(
            row=len(settings_vanilla) + len(settings_bukkit) + 2,
            column=0, columnspan=2, sticky="w", padx=10, pady=(20, 10)
        )

        # Define presets - Name, file, {setting: (default, optimized)}
        presets = [
            ( # Merge radius
                "Item/xp merge radius",
                "spigot",
                {
                    "      item: ": (0.5, 1.0),
                    "      exp: ": (-1.0, 1.0)
                }
            ),
            ( # Tracking range
                "Entity tracking range",
                "spigot",
                {
                    "      players: ": (128, 96),
                    "      animals: ": (96, 64),
                    "      monsters: ": (96, 64),
                    "      misc: ": (96, 64),
                    "      other: ": (64, 32)
                }
            ),
            ( # Activation range
                "Entity activation range",
                "spigot",
                {
                    "      animals: ": (32, 24),
                    "      monsters: ": (32, 24),
                    "      raiders: ": (64, 48),
                    "      misc: ": (16, 12),
                    "      water: ": (16, 12),
                    "      villagers: ": (32, 24),
                    "      flying-monsters: ": (32, 16)
                }
            ),
            ( # Hoppers
                "Hopper optimization",
                "spigot",
                {
                    "      hopper-amount: ": (1, 2),
                    "      hopper-transfer: ": (8, 16),
                    "      hopper-check: ": (1, 8)
                }
            ),
            ( # Item despawn
                "Item despawn rates",
                "spigot",
                {
                    "      item-despawn-rate: ": (6000, 3000),
                    "      arrow-despawn-rate: ": (1200, 300),
                    "      trident-despawn-rate: ": (1200, 600)
                }
            ),
            ( # Max TNT
                "Max TNT optimization",
                "spigot",
                {
                    "      max-tnt-per-tick: ": (100, 50)
                }
            ),
            ( # Growth rates
                "Crop growth rates",
                "spigot",
                {
                    "      cactus-modifier: ": (100, 80),
                    "      cane-modifier: ": (100, 80),
                    "      melon-modifier: ": (100, 80),
                    "      mushroom-modifier: ": (100, 80),
                    "      pumpkin-modifier: ": (100, 80),
                    "      sapling-modifier: ": (100, 80),
                    "      wheat-modifier: ": (100, 80),
                    "      carrot-modifier: ": (100, 80),
                    "      potato-modifier: ": (100, 80)
                }
            ),
            ( # Villagers
                "Villager optimization",
                "spigot",
                {
                    "      tick-inactive-villagers: ": (True, False),
                    "      villagers-work-immunity-after: ": (100, 200),
                    "      villagers-work-immunity-for: ": (20, 40)
                }
            ),
            ( # Wake up inactive mobs
                "Wake up inactive mobs",
                "spigot",
                {
                    "          animals-max-per-tick: ": (4, 2),
                    "          monsters-max-per-tick: ": (8, 4),
                    "          villagers-max-per-tick: ": (4, 2),
                    "          animals-every: ": (1200, 1800),
                    "          monsters-every: ": (400, 800),
                    "          villagers-every: ": (600, 1200)
                }
            )
        ]

        # Track applied presets
        self.applied_presets = {}

        # Add preset buttons
        start_row = len(settings_vanilla) + len(settings_bukkit) + 3
        for i, (name, file_type, options) in enumerate(presets):
            frame = tki.CTkFrame(self.optimizations_list)
            frame.grid(row=start_row + i, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

            preset_key = f"{file_type}_{name.lower().replace('/', '_').replace(' ', '_')}"

            # Label for the preset
            label = tki.CTkLabel(frame, text=name, anchor="w")
            label.grid(row=0, column=0, padx=10, pady=5, sticky="w")

            # Button to apply/restore the preset
            apply_button = tki.CTkButton(
                frame,
                text="Apply",
                width=100,
                command=lambda k=preset_key, f=file_type, o=options: self.toggle_preset(k, f, o)
            )
            apply_button.grid(row=0, column=1, padx=10, pady=5, sticky="e")
            # Configure the column weight to push the button to the right
            frame.grid_columnconfigure(0, weight=1)

            # Store button reference
            setattr(self, f"preset_btn_{preset_key}", apply_button)

        # Save button
        save_button = tki.CTkButton(
            self.optimizations_frame,
            text="Save Optimization Settings",
            command=self.save_optimization_settings
        )
        save_button.grid(row=2, column=0, padx=10, pady=10, sticky="ew")

    def toggle_preset(self, preset_key, file_type, options):
        """Toggle between applying and restoring a preset"""
        if not hasattr(self, 'applied_presets'):
            self.applied_presets = {}

        # Get the button
        button = getattr(self, f"preset_btn_{preset_key}")

        # Check if preset is already applied
        if preset_key in self.applied_presets and self.applied_presets[preset_key]:
            # Preset is applied, restore defaults
            self.applied_presets[preset_key] = False
            button.configure(text="Apply", fg_color=("#3B8ED0", "#1F6AA5"))  # Reset to default blue
            self.show_notification(f"Preset will be restored to defaults when saved")
        else:
            # Preset is not applied, apply optimized values
            self.applied_presets[preset_key] = True
            button.configure(text="Restore", fg_color="red")
            # Store the preset options for later use
            if not hasattr(self, 'preset_data'):
                self.preset_data = {}
            self.preset_data[preset_key] = options
            self.show_notification(f"Preset will be applied when saved")

    def save_optimization_settings(self):
        """Save optimization settings to config and apply to server files"""
        if not self.current_server:
            self.show_notification("No server selected", "error")
            return

        try:
            # Collect vanilla settings
            vanilla_settings = {}
            for key in ["simulation-distance", "entity-broadcast-range-percentage"]:
                widget = getattr(self, f"opt_vanilla_{key.replace('-', '_')}")
                vanilla_settings[key] = widget.get()

            # Collect bukkit settings
            bukkit_settings = {}
            for key in ["period-in-ticks", "monsters"]:
                widget = getattr(self, f"opt_bukkit_{key.replace('-', '_')}")
                bukkit_settings[key] = widget.get()

            # Build optimization config structure
            optimization_config = {
                "vanilla": vanilla_settings,
                "bukkit": bukkit_settings,
                "presets": {k: v for k, v in self.applied_presets.items() if v}
            }

            # Add preset data if available
            if hasattr(self, 'preset_data') and self.preset_data:
                optimization_config["preset_data"] = self.preset_data

            # Save to server config and apply settings
            result = self.current_server.save_optimization_settings(optimization_config)

            if result:
                self.show_notification("Optimization settings saved successfully")

                # If server is running, ask to restart
                if self.current_server.is_running():
                    self.ask_restart_server()
            else:
                self.show_notification("Failed to save optimization settings", "error")

        except Exception as e:
            self.show_notification(f"Error saving optimization settings: {str(e)}", "error")
            import traceback
            traceback.print_exc()

    def load_optimization_settings(self):
        """Load existing optimization settings and update UI"""
        if not hasattr(self, 'current_server') or not self.current_server or '-nogui' in self.args:
            return

        try:
            # Get optimization settings from server
            settings = self.current_server.get_optimization_settings()

            if not settings:
                print("No optimization settings found")
                return

            # Update vanilla settings
            if "vanilla" in settings:
                for key, value in settings["vanilla"].items():
                    widget_name = f"opt_vanilla_{key.replace('-', '_')}"
                    if hasattr(self, widget_name):
                        widget = getattr(self, widget_name)
                        widget.delete(0, tki.END)
                        widget.insert(0, str(value))

            # Update bukkit settings
            if "bukkit" in settings:
                for key, value in settings["bukkit"].items():
                    widget_name = f"opt_bukkit_{key.replace('-', '_')}"
                    if hasattr(self, widget_name):
                        widget = getattr(self, widget_name)
                        widget.delete(0, tki.END)
                        widget.insert(0, str(value))

            # Update preset buttons
            if "presets" in settings:
                self.applied_presets = {}
                for preset_key, applied in settings["presets"].items():
                    self.applied_presets[preset_key] = applied

                    button_name = f"preset_btn_{preset_key}"
                    if hasattr(self, button_name):
                        button = getattr(self, button_name)
                        if applied:
                            button.configure(text="Restore", fg_color="red")
                        else:
                            button.configure(text="Apply", fg_color=("#3B8ED0", "#1F6AA5"))

        except Exception as e:
            print(f"Error loading optimization settings: {e}")
            import traceback
            traceback.print_exc()

    # Resource monitoring
    def monitor_resources(self):    # sourcery skip: low-code-quality
        """Monitor and update resource usage periodically with improved stability"""
        max_points = 60  # Store last 60 data points
        cpu_count = psutil.cpu_count()  # Get number of CPU cores

        # Initialize with empty data
        self.cpu_data = []
        self.cpu_times = []
        self.ram_data = []
        self.ram_times = []

        # Track server state to detect changes
        was_running = False
        last_idx = 0  # Keep track of the last plot index

        if not hasattr(self, 'settings'):
            self.load_server_settings()

        max_ram_gb = float(self.settings.get('advanced',{}).get('memory', 4096)) / 1024

        if '-nogui' in self.args:
            console = ''
            is_terminal = os.isatty(sys.stdout.fileno())

            while self.running:
                try:
                    cpu_usage = self.current_server.get_cpu()
                    ram_usage = self.current_server.get_ram()

                    # Log console (only show new content)
                    new_console = self.current_server.get_console()
                    if new_console != console:
                        new_content = new_console.removeprefix(console)
                        if new_content:
                            # Clear current line before printing
                            if is_terminal:
                                sys.stdout.write("\r\033[K")  # Clear the line

                            # Print the new content
                            print(new_content, end='', flush=True)

                            # Add a new command prompt if needed
                            if is_terminal and not new_content.endswith('\n'):
                                print()  # Ensure we're on a new line

                            # Add command prompt after console output
                            print("> ", end='', flush=True)

                        console = new_console

                    # Normalize CPU usage and update resource display
                    normalized_cpu = cpu_usage / cpu_count if cpu_count > 0 else cpu_usage
                    ram_gb = ram_usage / 1024
                    ram_pct = ram_gb / max_ram_gb * 100

                    # Format resource usage message
                    status_msg = f'RESOURCE USAGE | CPU: {normalized_cpu:.1f}%, RAM: {ram_pct:.1f}% ({ram_gb:.1f}/{max_ram_gb:.1f} GB)'

                    # Display at top of terminal
                    if is_terminal:
                        self._update_cursor_and_display_status(status_msg)

                    time.sleep(0.1)
                except Exception as e:
                    print(f"Error monitoring resources: {e}", file=sys.stderr)
                    time.sleep(0.1)

            return


        while self.running:
            try:
                if not (hasattr(self, 'current_server') and self.current_server and self.tk_running):
                    time.sleep(0.5)
                    continue

                is_running = self.current_server.is_running()

                # Server state change detection
                server_state_changed = (was_running != is_running)
                was_running = is_running

                if is_running:
                    # Get CPU and RAM usage
                    try:
                        cpu_usage = self.current_server.get_cpu()
                        ram_usage = self.current_server.get_ram()

                        # Normalize CPU usage by cores and cap at 100%
                        normalized_cpu = cpu_usage / cpu_count if cpu_count > 0 else cpu_usage

                        # Calculate RAM percentage and cap at 100%
                        # Ensure max_ram is never 0 to avoid division by zero
                        if max_ram_gb <= 0:
                            # Use a reasonable default if max_ram is not available
                            max_ram_gb = 16
                            print(f"Warning: max_ram was 0 or negative. Set MAX ram to: {max_ram_gb} GB")

                        # Format RAM values for display
                        ram_gb = ram_usage / 1024

                        ram_pct = ram_gb / max_ram_gb * 100

                        # Update CPU value label safely
                        if hasattr(self, 'cpu_value_label') and self.running:
                            formatted_cpu = f"{normalized_cpu:.1f}%"
                            self.after(0, lambda text=formatted_cpu:
                                self.cpu_value_label.configure(text=text)
                                if hasattr(self, 'cpu_value_label') else None)

                        # Update RAM value label safely
                        if hasattr(self, 'ram_value_label') and self.running:
                            formatted_ram = f"{ram_pct:.1f}% ({ram_gb:.1f}/{max_ram_gb:.1f} GB)"
                            self.after(0, lambda text=formatted_ram:
                                self.ram_value_label.configure(text=text)
                                if hasattr(self, 'ram_value_label') else None)

                        # Update graph data - use continuous indexing instead of modulo
                        last_idx += 1

                        # Only add points if values are valid
                        if normalized_cpu >= 0:
                            self.cpu_times.append(last_idx)
                            self.cpu_data.append(min(100,normalized_cpu))

                        if ram_pct >= 0:
                            self.ram_times.append(last_idx)
                            self.ram_data.append(min(100,ram_pct))

                        # Keep only the most recent points
                        if len(self.cpu_times) > max_points:
                            self.cpu_times = self.cpu_times[-max_points:]
                            self.cpu_data = self.cpu_data[-max_points:]

                        if len(self.ram_times) > max_points:
                            self.ram_times = self.ram_times[-max_points:]
                            self.ram_data = self.ram_data[-max_points:]

                    except Exception as e:
                        print(f"Error collecting resource data: {e}")

                elif server_state_changed:
                    print("Dropping graph data.")

                    self.cpu_data = []
                    self.cpu_times = []
                    self.ram_data = []
                    self.ram_times = []
                    last_idx = 0  # Reset the counter too

                    # Reset labels
                    if hasattr(self, 'cpu_value_label') and self.running:
                        self.after(0, lambda:
                            self.cpu_value_label.configure(text="0.0%")
                            if hasattr(self, 'cpu_value_label') else None)

                    if hasattr(self, 'ram_value_label') and self.running:
                        self.after(0, lambda:
                            self.ram_value_label.configure(text="0 MB (0.00 GB, 0.0%)")
                            if hasattr(self, 'ram_value_label') else None)

                # Always update plots when UI exists
                if all(hasattr(self, attr) for attr in ['cpu_line', 'ram_line', 'cpu_canvas', 'ram_canvas']) and self.running:
                    try:
                        # Update the plot data
                        self.cpu_line.set_data(self.cpu_times, self.cpu_data)
                        self.ram_line.set_data(self.ram_times, self.ram_data)

                        # Adjust x-axis limits
                        if self.cpu_times or self.ram_times:
                            # Use the longest data array to determine x limits
                            latest_point = max(
                                self.cpu_times[-1] if self.cpu_times else 0,
                                self.ram_times[-1] if self.ram_times else 0
                            )
                            x_min = max(0, latest_point - 60)
                            x_max = max(60, latest_point)
                            self.cpu_ax.set_xlim(x_min, x_max)
                            self.ram_ax.set_xlim(x_min, x_max)
                        else:
                            self.cpu_ax.set_xlim(0, 60)
                            self.ram_ax.set_xlim(0, 60)

                        # Always use 0-100% range for y-axis
                        self.cpu_ax.set_ylim(0, 100)
                        self.ram_ax.set_ylim(0, 110)

                        # Redraw the canvases
                        if self.tk_running:
                            self.cpu_canvas.draw_idle()
                            self.ram_canvas.draw_idle()
                    except Exception as e:
                        print(f"Error updating plots: {e}")
                        traceback.print_exc()

            except Exception as e:
                print(f"Error in monitor_resources: {e}")
                traceback.print_exc()

            # Sleep interval
            time.sleep(0.5)  # Update every half second

    def _update_cursor_and_display_status(self, status_msg):
        # Save cursor position
        sys.stderr.write('\033[s')
        # Go to the beginning of the first line
        sys.stderr.write('\033[1;1H')
        # Clear the line
        sys.stderr.write('\033[K')
        # Print resource usage
        sys.stderr.write(status_msg)
        # Restore cursor position (where user might be typing)
        sys.stderr.write('\033[u')
        sys.stderr.flush()

    def update_dashboard(self):
        """Update dashboard with current server information"""
        if not self.current_server:
            return

        # Update server status
        is_running = self.current_server.is_running()
        self.status_value.configure(
            text="Running" if is_running else "Stopped",
            text_color="green" if is_running else "red"
        )

        # Update server version
        server_version = self.current_server.get_version() or "Unknown"
        self.version_value.configure(text=server_version)

        # Update player count
        player_count = len(self.current_server.get_players()) if is_running else 0
        max_players = self.current_server.get_max_players() or "?"
        self.players_value.configure(text=f"{player_count}/{max_players}")

        # Update uptime
        uptime = self.current_server.get_uptime() if is_running else 0
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime_value.configure(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def update_console(self):
        """Update console text with latest output"""
        if not (
                hasattr(self, 'current_server')
                and self.current_server
                and hasattr(self, 'console_output')
            ):
            # Schedule next update
            self.after(100, self.update_console)
            return
        else:
            console_text = self.current_server.get_console()

        if console_text == self.console_output.get("1.0", tki.END).strip():
            # Schedule next update
            self.after(100, self.update_console)
            return

        # Save current position
        current_pos = self.console_output.yview()

        self.console_output.configure(state="normal")
        self.console_output.delete("1.0", tki.END)

        # Setup text tags for ANSI colors if they don't exist yet
        if not hasattr(self, 'console_tags_configured'):
            self._setup_console_tags()

        # Parse ANSI color codes and insert with appropriate tags
        self._insert_colored_text(console_text)

        # Handle auto-scrolling
        self._handle_console_scroll(current_pos)

        self.console_output.configure(state="disabled")

        # Schedule next update
        self.after(100, self.update_console)

    def _setup_console_tags(self):
        """Configure tags for ANSI color codes"""
        # Basic colors
        color_map = {
            "red": "red",
            "green": "green",
            "yellow": "yellow",
            "blue": "blue",
            "magenta": "magenta",
            "cyan": "cyan",
            "white": "white",
            "bright_red": "#ff5555",
            "bright_green": "#55ff55",
            "bright_yellow": "#ffff55",
            "bright_blue": "#5555ff",
            "bright_magenta": "#ff55ff",
            "bright_cyan": "#55ffff",
            "bright_white": "#ffffff"
        }

        for tag_name, color in color_map.items():
            self.console_output._textbox.tag_configure(tag_name, foreground=color)

        # Initialize RGB tag tracking
        self.rgb_tags = set()
        self.console_tags_configured = True

    def _insert_colored_text(self, console_text):
        """Parse ANSI codes and insert text with appropriate formatting"""
        import re
        ansi_pattern = re.compile(r'\033\[([\d;]*)m')
        current_pos = 0
        current_tag = None

        # Map ANSI codes to tag names
        ansi_code_map = {
            '31': "red",
            '32': "green",
            '33': "yellow",
            '34': "blue",
            '35': "magenta",
            '36': "cyan",
            '37': "white",
            '91': "bright_red",
            '92': "bright_green",
            '93': "bright_yellow",
            '94': "bright_blue",
            '95': "bright_magenta",
            '96': "bright_cyan",
            '97': "bright_white"
        }

        # Find all ANSI codes
        for match in ansi_pattern.finditer(console_text):
            # Insert text before the ANSI code with previous tag
            if match.start() > current_pos:
                text = console_text[current_pos:match.start()]
                if current_tag:
                    self.console_output._textbox.insert(tki.END, text, current_tag)
                else:
                    self.console_output.insert(tki.END, text)

            # Process the ANSI code to determine the new tag
            code = match.group(1)

            if code == '0' or code == '':
                current_tag = None  # Reset to default
            elif code.startswith('38;2;'):  # RGB foreground color
                current_tag = self._process_rgb_code(code)
            elif code in ansi_code_map:
                current_tag = ansi_code_map[code]

            current_pos = match.end()

        # Insert any remaining text
        if current_pos < len(console_text):
            text = console_text[current_pos:]
            if current_tag:
                self.console_output._textbox.insert(tki.END, text, current_tag)
            else:
                self.console_output.insert(tki.END, text)

    def _process_rgb_code(self, code):
        """Process RGB ANSI color code (38;2;R;G;B)"""
        try:
            parts = code.split(';')
            if len(parts) >= 5:  # Make sure we have all parts
                r, g, b = int(parts[2]), int(parts[3]), int(parts[4])
                rgb_tag = f"rgb_{r}_{g}_{b}"  # Create a unique tag name

                # Check if we need to create this color tag
                if rgb_tag not in self.rgb_tags:
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"
                    self.console_output._textbox.tag_configure(rgb_tag, foreground=hex_color)
                    self.rgb_tags.add(rgb_tag)  # Remember we created this tag

                return rgb_tag
        except Exception:
            pass
        return None

    def _handle_console_scroll(self, current_pos):
        """Handle auto-scrolling of console output"""
        try:
            # Check if current_pos is a tuple with at least 2 elements
            if isinstance(current_pos, tuple) and len(current_pos) >= 2:
                if current_pos[1] > 0.9:
                    self.console_output.see(tki.END)
                else:
                    self.console_output.yview_moveto(current_pos[0])
            else:
                # Default to scrolling to the end
                self.console_output.see(tki.END)
        except (TypeError, IndexError):
            # If any error occurs during scrolling, just scroll to end
            self.console_output.see(tki.END)

    def update_console_periodic(self):
        """Update console output periodically"""
        while self.running:
            try:
                self.update_console()
            except Exception:
                pass  # Handle any exceptions in case the UI is being rebuilt
            time.sleep(1)

    def send_command(self, event=None):
        """Send command to server from Enter key event"""
        self.send_command_button()
        return "break"  # Prevent default Enter behavior

    def send_command_button(self):
        """Send command to server"""
        if not self.current_server:
            return

        command = self.console_input.get().strip()
        if command:
            self.current_server.send_command(command)
            self.console_input.delete(0, tki.END)  # Clear input field

            # Update console after a short delay to see command output
            self.after(200, self.update_console)

    def clear_console(self):
        """Clear console output"""
        self.console_output.configure(state="normal")
        self.console_output.delete("1.0", tki.END)
        self.console_output.configure(state="disabled")

    def update_players(self):
        """Update players list with current online players"""
        if not self.current_server:
            return

        # Initialize player_frames list if it doesn't exist
        if not hasattr(self, 'player_frames'):
            self.player_frames = []

        # Initialize previous_players set if it doesn't exist
        if not hasattr(self, 'previous_players'):
            self.previous_players = set()

        # Get current players
        players = self.current_server.get_players()
        current_players = set(players)

        # If player list hasn't changed, don't rebuild the UI
        if current_players == self.previous_players:
            return

        # Store current players for next comparison
        self.previous_players = current_players

        # Clear existing player frames
        for frame in self.player_frames:
            frame.destroy()
        self.player_frames = []

        # If no players, show a message
        if not players:
            no_players_label = tki.CTkLabel(
                self.players_list,
                text="No players online"
            )
            no_players_label.pack(padx=20, pady=20)
            self.player_frames.append(no_players_label)
            return

        # Add player frames
        for player in players:
            player_frame = tki.CTkFrame(self.players_list)
            player_frame.pack(fill=tki.X, padx=5, pady=5)

            # Configure columns to ensure proper button placement
            player_frame.columnconfigure(0, weight=1)  # Name takes most space
            player_frame.columnconfigure(1, weight=0)  # Buttons take minimum space
            player_frame.columnconfigure(2, weight=0)
            player_frame.columnconfigure(3, weight=0)

            # Player name
            player_name = tki.CTkLabel(
                player_frame,
                text=player,
                font=tki.CTkFont(weight="bold")
            )
            player_name.grid(
                row=0,
                column=0,
                padx=10,
                pady=10,
                sticky="w"
            )

            # Control buttons with fixed width to ensure consistent layout
            kick_btn = tki.CTkButton(
                player_frame,
                text="Kick",
                width=60,
                command=lambda p=player: self.kick_player(p)
            )
            kick_btn.grid(
                row=0,
                column=1,
                padx=(5, 5),
                pady=5
            )

            ban_btn = tki.CTkButton(
                player_frame,
                text="Ban",
                width=60,
                fg_color="red",
                command=lambda p=player: self.ban_player(p)
            )
            ban_btn.grid(
                row=0,
                column=2,
                padx=(5, 5),
                pady=5
            )

            op_btn = tki.CTkButton(
                player_frame,
                text="Op",
                width=60,
                fg_color="orange",
                command=lambda p=player: self.op_player(p)
            )
            op_btn.grid(
                row=0,
                column=3,
                padx=(5, 5),
                pady=5
            )

            self.player_frames.append(player_frame)

    # Plugins
    def reload_plugins(self):
        """Reload all plugins on the server"""
        if self.current_server and self.current_server.is_running():
            self.current_server.send_command("reload confirm")
            self.update_plugins()
            # Show confirmation message
            self.show_notification("Plugins reloaded successfully")
        else:
            self.show_notification("Server must be running to reload plugins", "error")

    def install_plugin(self):
        """Install a new plugin from a .jar file"""
        if not self.current_server:
            return

        import tkinter.filedialog as filedialog

        # Open file dialog to select .jar file
        filetypes = [("Plugin files", "*.jar"), ("All files", "*.*")]
        plugin_path = filedialog.askopenfilename(
            title="Select Plugin JAR File",
            filetypes=filetypes
        )

        if plugin_path:
            import shutil
            import os

            # Get plugins directory for current server
            server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "servers", self.current_server.name)
            plugins_dir = os.path.join(server_dir, "plugins")

            # Create plugins directory if it doesn't exist
            if not os.path.exists(plugins_dir):
                os.makedirs(plugins_dir)

            # Copy plugin to plugins directory
            dest_path = os.path.join(plugins_dir, os.path.basename(plugin_path))
            try:
                shutil.copy2(plugin_path, dest_path)
                self.show_notification(f"Plugin {os.path.basename(plugin_path)} installed successfully")

                # If server is running, ask if user wants to reload plugins
                if self.current_server.is_running():
                    self.ask_reload_plugins()

                # Update plugins list
                self.update_plugins()
            except Exception as e:
                self.show_notification(f"Error installing plugin: {str(e)}", "error")

    def update_plugins(self):
        """Update the list of installed plugins"""
        if not self.current_server:
            return

        # Clear existing plugin frames
        for frame in self.plugin_frames:
            frame.destroy()
        self.plugin_frames = []

        # Get plugins from server
        plugins = self.current_server.get_plugins()

        if not plugins:
            no_plugins_label = tki.CTkLabel(self.plugins_list_frame, text="No plugins installed")
            no_plugins_label.pack(padx=20, pady=20)
            self.plugin_frames.append(no_plugins_label)
            return

        # Add plugin frames
        for plugin_name, plugin_data in plugins.items():
            plugin_frame = tki.CTkFrame(self.plugins_list_frame)
            plugin_frame.pack(fill=tki.X, padx=5, pady=5)

            # Plugin name and version
            name_version = f"{plugin_name} v{plugin_data.get('version', 'Unknown')}"
            plugin_name_label = tki.CTkLabel(plugin_frame, text=name_version,
                                            font=tki.CTkFont(weight="bold"))
            plugin_name_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

            # Plugin status (enabled/disabled)
            status_text = "Enabled" if plugin_data.get("enabled", False) else "Disabled"
            status_color = "green" if plugin_data.get("enabled", False) else "red"
            plugin_status = tki.CTkLabel(plugin_frame, text=status_text, text_color=status_color)
            plugin_status.grid(row=0, column=1, padx=10, pady=(10, 5), sticky="w")

            # Plugin author if available
            if "author" in plugin_data:
                author_text = f"by {plugin_data['author']}"
                plugin_author = tki.CTkLabel(plugin_frame, text=author_text)
                plugin_author.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="w")

            # Plugin description if available
            if "description" in plugin_data:
                plugin_desc = tki.CTkLabel(plugin_frame, text=plugin_data["description"],
                                          wraplength=400, justify="left")
                plugin_desc.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")

            # Control buttons
            buttons_frame = tki.CTkFrame(plugin_frame)
            buttons_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")

            # Enable/disable button
            if plugin_data.get("enabled", False):
                toggle_btn = tki.CTkButton(buttons_frame, text="Disable",
                                          fg_color="gray",
                                          command=lambda p=plugin_name: self.disable_plugin(p))
            else:
                toggle_btn = tki.CTkButton(buttons_frame, text="Enable",
                                          fg_color="green",
                                          command=lambda p=plugin_name: self.enable_plugin(p))
            toggle_btn.pack(side=tki.LEFT, padx=5, pady=5)

            # Delete button
            delete_btn = tki.CTkButton(buttons_frame, text="Delete",
                                      fg_color="red",
                                      command=lambda p=plugin_name: self.delete_plugin(p))
            delete_btn.pack(side=tki.LEFT, padx=5, pady=5)

            self.plugin_frames.append(plugin_frame)

    # Backups
    def create_backup(self):
        """Create a new backup of the server"""
        if not self.current_server:
            return

        import datetime

        # Show progress dialog
        self.backup_progress = tki.CTkToplevel(self)
        self.backup_progress.title("Creating Backup")
        self.backup_progress.geometry("300x100")
        self.backup_progress.transient(self)
        self.backup_progress.grab_set()

        progress_label = tki.CTkLabel(self.backup_progress, text="Creating backup...")
        progress_label.pack(pady=(20, 10))

        progress_bar = tki.CTkProgressBar(self.backup_progress, width=250)
        progress_bar.pack(pady=(0, 10))
        progress_bar.set(0.5)  # Indeterminate progress

        # Create backup in a separate thread
        def do_backup():
            try:
                backup_name = f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                self.current_server.create_backup(backup_name)
                self.after(100, finish_backup)
            except Exception as e:
                self.after(100, finish_backup, str(e))

        def finish_backup(error=None):
            self.backup_progress.destroy()
            if error:
                self.show_notification(f"Backup failed: {error}", "error")
            else:
                self.show_notification("Backup created successfully")
                self.update_backups()

        threading.Thread(target=do_backup, daemon=True).start()

    def update_backups(self):
        """Update the list of server backups"""
        if not self.current_server:
            return

        # Clear existing backup frames
        for widget in self.backups_list_frame.winfo_children():
            widget.destroy()

        # Get backups for the current server
        backups = self.current_server.get_backups()

        if not backups:
            no_backups_label = tki.CTkLabel(self.backups_list_frame, text="No backups found")
            no_backups_label.pack(padx=20, pady=20)
            return

        # Create header row
        header_frame = tki.CTkFrame(self.backups_list_frame)
        header_frame.pack(fill=tki.X, padx=5, pady=5)

        tki.CTkLabel(header_frame, text="Backup Name", font=tki.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        tki.CTkLabel(header_frame, text="Date Created", font=tki.CTkFont(weight="bold")).grid(row=0, column=1, padx=10, pady=5, sticky="w")
        tki.CTkLabel(header_frame, text="Size", font=tki.CTkFont(weight="bold")).grid(row=0, column=2, padx=10, pady=5, sticky="w")
        tki.CTkLabel(header_frame, text="Actions", font=tki.CTkFont(weight="bold")).grid(row=0, column=3, padx=10, pady=5, sticky="w")

        # Sort backups by date (newest first)
        sorted_backups = sorted(backups, key=lambda x: x.get("timestamp", 0), reverse=True)

        # Add backup entries
        for backup in sorted_backups:
            backup_frame = tki.CTkFrame(self.backups_list_frame)
            backup_frame.pack(fill=tki.X, padx=5, pady=5)

            # Backup name
            backup_name = tki.CTkLabel(backup_frame, text=backup.get("name", "Unknown"))
            backup_name.grid(row=0, column=0, padx=10, pady=10, sticky="w")

            # Date created
            import datetime
            timestamp = backup.get("timestamp", 0)
            date_str = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            date_label = tki.CTkLabel(backup_frame, text=date_str)
            date_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")

            # Size
            size_bytes = backup.get("size", 0)
            size_mb = round(size_bytes / (1024 * 1024), 2)
            size_label = tki.CTkLabel(backup_frame, text=f"{size_mb} MB")
            size_label.grid(row=0, column=2, padx=10, pady=10, sticky="w")

            # Action buttons
            buttons_frame = tki.CTkFrame(backup_frame)
            buttons_frame.grid(row=0, column=3, padx=10, pady=5)

            restore_btn = tki.CTkButton(buttons_frame, text="Restore",
                                      command=lambda b=backup["name"]: self.restore_backup(b))
            restore_btn.grid(row=0, column=0, padx=5, pady=5)

            delete_btn = tki.CTkButton(buttons_frame, text="Delete", fg_color="red",
                                     command=lambda b=backup["name"]: self.delete_backup(b))
            delete_btn.grid(row=0, column=1, padx=5, pady=5)

    def save_backup_schedule(self):
        """Save backup schedule settings"""
        if not self.current_server:
            return

        enabled = self.after_enabled.get()
        interval = int(self.after_interval.get())
        max_backups = self.max_backups.get()

        # If "All" is selected, use -1 to indicate no limit
        if max_backups == "All":
            max_backups = -1
        else:
            max_backups = int(max_backups)

        # Save schedule to server configuration
        self.current_server.set_backup_schedule(enabled, interval, max_backups)
        self.show_notification("Backup schedule saved")

        # If enabled, schedule the first backup
        if enabled:
            self.current_server.schedule_next_backup()

    def save_settings(self):
        """Save server settings from all tabs"""
        if not self.current_server:
            return

        # Collect settings from General tab
        general_settings = {}
        for setting_name in ["server-name", "motd", "server-port", "max-players",
                            "view-distance", "gamemode", "difficulty"]:
            # Use consistent attribute naming by not replacing hyphens
            attr_name = f"setting_{setting_name}"
            if hasattr(self, attr_name):
                widget = getattr(self, attr_name)
                if hasattr(widget, "get"):
                    general_settings[setting_name] = widget.get()

        # Collect settings from World tab
        world_settings = {}
        for setting_name in ["level-seed", "level-type"]:
            # Use consistent attribute naming by not replacing hyphens
            attr_name = f"setting_{setting_name}"
            if hasattr(self, attr_name):
                widget = getattr(self, attr_name)
                if hasattr(widget, "get"):
                    world_settings[setting_name] = widget.get()

        # Boolean settings from World tab
        for setting_name in ["generate-structures", "allow-nether", "spawn-npcs",
                            "spawn-animals", "spawn-monsters"]:
            # Use consistent attribute naming by not replacing hyphens
            attr_name = f"setting_{setting_name}"
            if hasattr(self, attr_name):
                widget = getattr(self, attr_name)
                if hasattr(widget, "get"):
                    world_settings[setting_name] = widget.get()

        # Collect settings from Advanced tab
        advanced_settings = {}
        for setting_name in ["memory"]:
            # Use consistent attribute naming
            attr_name = f"setting_{setting_name}"
            if hasattr(self, attr_name):
                widget = getattr(self, attr_name)
                if hasattr(widget, "get"):
                    advanced_settings[setting_name] = widget.get()

        # Boolean settings from Advanced tab
        for setting_name in ["enable-command-block", "pvp",
                            "force-gamemode", "allow-flight"]:
            # Use consistent attribute naming by not replacing hyphens
            attr_name = f"setting_{setting_name}"
            if hasattr(self, attr_name):
                widget = getattr(self, attr_name)
                if hasattr(widget, "get"):
                    advanced_settings[setting_name] = widget.get()

        # Save settings to server
        try:
            self.current_server.update_settings(
                general=general_settings,
                world=world_settings,
                advanced=advanced_settings
            )
            self.show_notification("Server settings saved successfully")

            # Ask to restart server for settings to take effect if server is running
            if self.current_server.is_running():
                self.ask_restart_server()
        except Exception as e:
            self.show_notification(f"Error saving settings: {str(e)}", "error")

    def show_notification(self, message, level="info"):
        """Show a notification message"""
        import tkinter.messagebox as messagebox

        if level == "error":
            messagebox.showerror("Error", message)
        elif level == "warning":
            messagebox.showwarning("Warning", message)
        else:
            messagebox.showinfo("Information", message)

    def ask_reload_plugins(self):
        """Ask user if they want to reload plugins"""
        import tkinter.messagebox as messagebox

        result = messagebox.askyesno(
            "Reload Plugins",
            "Would you like to reload the plugins now?"
        )

        if result:
            self.reload_plugins()

    def ask_restart_server(self):
        """Ask user if they want to restart the server"""
        import tkinter.messagebox as messagebox

        result = messagebox.askyesno(
            "Restart Server",
            "Settings have been saved. Would you like to restart the server now for changes to take effect?"
        )

        if result:
            self.restart_server()

    # Player management methods
    def kick_player(self, player_name):
        """Kick a player from the server"""
        if self.current_server and self.current_server.is_running():
            self.current_server.send_command(f"kick {player_name}")
            self.after(500, self.update_players)  # Update player list after a delay

    def ban_player(self, player_name:str):
        """Ban a player from the server"""
        if self.current_server and self.current_server.is_running():
            self.current_server.send_command(f"ban {player_name}")
            self.after(500, self.update_players)

    def op_player(self, player_name:str):
        """Give operator status to a player"""
        if self.current_server and self.current_server.is_running():
            self.current_server.send_command(f"op {player_name}")
            self.show_notification(f"Player {player_name} is now an operator")

    # Plugin management methods
    def enable_plugin(self, plugin_name:str):
        """Enable a plugin"""
        if self.current_server and self.current_server.is_running():
            result = self.current_server.enable_plugin(plugin_name)
            if result:
                self.show_notification(f"Plugin {plugin_name} enabled")
                self.update_plugins()
            else:
                self.show_notification(f"Failed to enable plugin {plugin_name}", "error")

    def disable_plugin(self, plugin_name:str):
        """Disable a plugin"""
        if self.current_server and self.current_server.is_running():
            result = self.current_server.disable_plugin(plugin_name)
            if result:
                self.show_notification(f"Plugin {plugin_name} disabled")
                self.update_plugins()
            else:
                self.show_notification(f"Failed to disable plugin {plugin_name}", "error")

    def delete_plugin(self, plugin_name:str):
        """Delete a plugin"""
        import tkinter.messagebox as messagebox

        result = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the plugin {plugin_name}?"
        )

        if result and self.current_server:
            if self.current_server.delete_plugin(plugin_name):
                self.show_notification(f"Plugin {plugin_name} deleted")
                self.update_plugins()
            else:
                self.show_notification(f"Failed to delete plugin {plugin_name}", "error")

    # Backup management methods
    def restore_backup(self, backup_name:str):
        """Restore a server backup"""
        import tkinter.messagebox as messagebox

        if not self.current_server:
            return

        # Confirm server needs to be stopped
        if self.current_server.is_running():
            result = messagebox.askyesno(
                "Stop Server",
                "Server must be stopped to restore a backup. Stop server now?"
            )
            if not result:
                return
            self.current_server.stop()
            # Wait for server to stop
            self.after(1000, lambda: self._do_restore_backup(backup_name))
        else:
            self._do_restore_backup(backup_name)

    def _do_restore_backup(self, backup_name:str):
        """Internal method to restore backup after server is stopped"""
        # Show progress dialog
        self.restore_progress = tki.CTkToplevel(self)
        self.restore_progress.title("Restoring Backup")
        self.restore_progress.geometry("300x100")
        self.restore_progress.transient(self)
        self.restore_progress.grab_set()

        progress_label = tki.CTkLabel(self.restore_progress, text="Restoring backup...")
        progress_label.pack(pady=(20, 10))

        progress_bar = tki.CTkProgressBar(self.restore_progress, width=250)
        progress_bar.pack(pady=(0, 10))
        progress_bar.set(0.5)  # Indeterminate progress

        # Restore backup in a separate thread
        def do_restore():
            try:
                self.current_server.restore_backup(backup_name)
                self.after(100, finish_restore)
            except Exception as e:
                self.after(100, lambda: finish_restore(str(e)))

        def finish_restore(error=None):
            self.restore_progress.destroy()
            if error:
                self.show_notification(f"Restore failed: {error}", "error")
            else:
                self.show_notification("Backup restored successfully")
                # Ask if user wants to start server again
                self.ask_start_server()

        threading.Thread(target=do_restore, daemon=True).start()

    def delete_backup(self, backup_name:str):
        """Delete a server backup"""
        import tkinter.messagebox as messagebox

        result = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the backup {backup_name}?"
        )

        if result and self.current_server:
            if self.current_server.delete_backup(backup_name):
                self.show_notification(f"Backup {backup_name} deleted")
                self.update_backups()
            else:
                self.show_notification(f"Failed to delete backup {backup_name}", "error")

    def ask_start_server(self):
        """Ask user if they want to start the server"""
        import tkinter.messagebox as messagebox

        result = messagebox.askyesno(
            "Start Server",
            "Would you like to start the server now?"
        )

        if result:
            self.start_server()

args = sys.argv[1:]

app = MCManager(args)
app.run()
