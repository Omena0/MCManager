from mcstatus import JavaServer
from datetime import datetime
import subprocess
import threading
import traceback
import zipfile
import psutil
import shutil
import ctypes
import time
import json
import glob
import re
import os

def is_admin():
    """
    Check if the program is running with administrator privileges.

    Returns:
        bool: True if running as admin, False otherwise
    """
    try:
        # Windows-specific check
        if os.name == 'nt':
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            # Unix-like systems check (root has UID 0)
            return os.geteuid() == 0
    except:
        return False

launch_args = '-Xmx<RAM>M -Xms<RAM>M -XX:+UnlockExperimentalVMOptions -XX:+UnlockDiagnosticVMOptions -XX:+AlwaysPreTouch -XX:+DisableExplicitGC -XX:+UseNUMA -XX:NmethodSweepActivity=1 -XX:ReservedCodeCacheSize=400M -XX:NonNMethodCodeHeapSize=12M -XX:ProfiledCodeHeapSize=194M -XX:NonProfiledCodeHeapSize=194M -XX:-DontCompileHugeMethods -XX:MaxNodeLimit=240000 -XX:NodeLimitFudgeFactor=8000 -XX:+UseVectorCmov -XX:+PerfDisableSharedMem -XX:+UseFastUnorderedTimeStamps -XX:+UseCriticalJavaThreadPriority -XX:ThreadPriorityPolicy=1 -XX:AllocatePrefetchStyle=3  -XX:+UseG1GC -XX:MaxGCPauseMillis=37 -XX:+PerfDisableSharedMem -XX:G1HeapRegionSize=16M -XX:G1NewSizePercent=23 -XX:G1ReservePercent=20 -XX:SurvivorRatio=32 -XX:G1MixedGCCountTarget=3 -XX:G1HeapWastePercent=20 -XX:InitiatingHeapOccupancyPercent=10 -XX:G1RSetUpdatingPauseTimePercent=0 -XX:MaxTenuringThreshold=1 -XX:G1SATBBufferEnqueueingThresholdPercent=30 -XX:G1ConcMarkStepDurationMillis=5.0 -XX:GCTimeRatio=99'

# Enable large pages, as they require administrators.
if is_admin():
    launch_args += ' -XX:+UseLargePages -XX:LargePageSizeInBytes=2m'
    print('Enabling large pages because we are running as admin.')


class Server:
    def __init__(self, name, max_ram=4096):
        self.name = name
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "servers", name))
        self.jar_path = self._find_server_jar()
        self.process = None
        self.console_output = []
        self._is_running = False
        self.start_time = None
        self._monitor_thread = None
        self._console_lock = threading.Lock()

        # Create server directory if it doesn't exist
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

        # Resource monitoring data
        self.cpu_usage = 0
        self.ram_usage = 0
        self.max_ram = max_ram # MB

        # Player tracking
        self.players = []
        self.status = None
        self.max_players = 0

    def _find_server_jar(self):
        """Find a server jar file in the server directory"""
        jar_files = glob.glob(os.path.join(self.base_dir, "*.jar"))
        if jar_files:
            return jar_files[0]  # Return the first jar file found
        return None

    def start(self):
        """Start the Minecraft server"""
        if self._is_running:
            return True

        self.jar_path = self._find_server_jar()

        if not self.jar_path:
            print(f"No server jar found in {self.base_dir}")
            return False

        try:
            # Create eula.txt if it doesn't exist (agree to EULA)
            eula_path = os.path.join(self.base_dir, "eula.txt")
            if not os.path.exists(eula_path):
                with open(eula_path, "w") as f:
                    f.write("eula=true\n")

            # Get memory settings from server.properties or use default
            memory = self._get_memory_setting()

            print(f'Starting server with {memory} MB')

            # Build Java command
            cmd = [
                java_path,
                *launch_args.replace("<RAM>", str(memory)).split(),
                "-jar",
                self.jar_path.replace('\\','/'),
                "nogui"
            ]

            # Start server process
            self.process = subprocess.Popen(
                cmd,
                cwd=self.base_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            self._is_running = True
            self.start_time = time.time()

            # Start monitoring thread
            self._start_monitor()

            # Start console reader thread
            threading.Thread(target=self._read_console, daemon=True).start()

            return True
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self._is_running = False
            return False

    def stop(self):
        """Stop the Minecraft server"""
        if not self._is_running or not self.process:
            return True

        try:
            # Send stop command
            self.send_command("stop")

            # Give the server some time to shut down gracefully
            for _ in range(30):  # Wait up to 30 seconds
                if not self.is_running():
                    break
                time.sleep(1)

            # If server is still running, terminate process
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)

            self._is_running = False
            return True
        except Exception as e:
            print(f"Failed to stop server: {str(e)}")
            # Force kill as last resort
            if self.process:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self._is_running = False
            return False

    def restart(self):
        """Restart the Minecraft server"""
        if self.stop():
            time.sleep(2)  # Give some time between stop and start
            return self.start()
        return False

    def is_running(self):
        """Check if the server is running"""
        return self._is_running

    def send_command(self, command:str):
        """Send a command to the server console"""
        if not self._is_running or not self.process:
            return False

        try:
            self.process.stdin.write(f"{command}\n")
            self.process.stdin.flush()
            return True
        except Exception as e:
            print(f"Failed to send command: {str(e)}")
            return False

    def get_console(self):
        """Get the console output"""
        with self._console_lock:
            return "\n".join(self.console_output[-1000:])  # Limit to last 1000 lines

    def _read_console(self):
        """Read console output from the server process and track player activity"""
        if not self.process:
            return

        try:
            for line in iter(self.process.stdout.readline, ''):
                if not line:
                    break

                with self._console_lock:
                    self.console_output.append(line.strip())
                    # Keep console buffer at a reasonable size
                    if len(self.console_output) > 5000:
                        self.console_output = self.console_output[-4000:]

        except Exception as e:
            print(f"Error reading console: {str(e)}")
        finally:
            self._is_running = False

    def update_status(self):
        try:
            server = JavaServer(self.get_ip(), self.get_port())
            self.status = server.status()

            # Get player count
            player_count = self.status.players.online
            self.max_players  = self.status.players.max

            # Get player names (if available - some servers disable this)
            self.players = []
            if self.status.players.sample:
                self.players = [player.name for player in self.status.players.sample]

            return {
                "online": player_count,
                "max": self.max_players,
                "players": self.players,
                "version": self.status.version.name
            }

        except Exception as e:
            print(f"Error querying server: {e}")
            return {"online": 0, "max": 0, "players": []}
            self.players = []
            self.max_players = 0
            self.status = None

    def get_players(self):
        """Get list of online players using active tracking"""
        if not self._is_running:
            return []

        self.update_status()

        return list(self.players)

    def get_max_players(self):
        """Get the maximum number of players allowed"""
        if self._is_running:
            self.update_status()
            return self.max_players

        # Get from server.properties
        try:
            prop_file = os.path.join(self.base_dir, "server.properties")
            if os.path.exists(prop_file):
                with open(prop_file, 'r') as f:
                    for line in f:
                        if line.startswith("max-players="):
                            return line.split('=')[1].strip()
        except:
            return 0

    def get_version(self):
        """Get the server version"""
        self.update_status()
        return self.status.version.name

    def get_uptime(self):
        """Get server uptime in seconds"""
        if self._is_running and self.start_time:
            return time.time() - self.start_time
        return 0

    def get_ip(self):
        """Get the server IP address"""
        try:
            prop_file = os.path.join(self.base_dir, "server.properties")
            if os.path.exists(prop_file):
                with open(prop_file, 'r') as f:
                    for line in f:
                        if line.startswith("server-ip="):
                            return line.split('=')[1].strip()
        except:
            return "127.0.0.1"

    def get_port(self):
        """Get the server port"""
        try:
            prop_file = os.path.join(self.base_dir, "server.properties")
            if os.path.exists(prop_file):
                with open(prop_file, 'r') as f:
                    for line in f:
                        if line.startswith("server-port="):
                            return int(line.split('=')[1].strip())
            return 25565  # Default port
        except:
            return 25565

    def _get_memory_setting(self):
        """Get memory allocation from config or use default"""
        if not hasattr(self, 'max_ram'):
            self.max_ram = 4096
        try:
            # Check for custom memory setting
            config_file = os.path.join(self.base_dir, "server_config.json")
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    if "memory" in config['advanced']:
                        memory = int(config["advanced"]["memory"])
                        self.max_ram = memory
                        return memory

            # Default to 4GB
        except Exception:
            ...
        return self.max_ram

    def _start_monitor(self):
        """Start monitoring server resources"""
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
            self._monitor_thread.start()

    def _monitor_resources(self):
        """Monitor server CPU and RAM usage"""
        while self._is_running and self.process:
            start = time.time()
            try:
                if self.process.poll() is None:  # Process still running
                    proc = psutil.Process(self.process.pid)

                    # Get CPU usage - Fix by calling with interval
                    self.cpu_usage = proc.cpu_percent(interval=0.5)  # Use interval parameter

                    # Get RAM usage (convert from bytes to MB)
                    self.ram_usage = proc.memory_info().rss / (1024 * 1024)

                    # Also include child processes (Java may have child processes)
                    for child in proc.children(recursive=True):
                        try:
                            self.cpu_usage += child.cpu_percent(interval=0)
                            self.ram_usage += child.memory_info().rss / (1024 * 1024)
                        except Exception:
                            pass

            except Exception:
                pass

            time.sleep(max(0,0.5-(time.time()-start)))  # Sleep to maintain loop interval

        try:
            self.process.terminate()
        except Exception:
            ...

    def get_cpu(self):
        """Get server CPU usage percentage"""
        return self.cpu_usage if self._is_running else 0

    def get_ram(self):
        """Get server RAM usage in MB"""
        return self.ram_usage if self._is_running else 0

    def get_max_ram(self):
        """Get maximum RAM allocation in MB"""
        return self.max_ram

    # Plugin management methods
    def get_plugins(self):
        """Get list of installed plugins"""
        plugins = {}
        plugins_dir = os.path.join(self.base_dir, "plugins")

        if not os.path.exists(plugins_dir):
            return plugins

        # Look for jar files in plugins directory
        for jar_file in glob.glob(os.path.join(plugins_dir, "*.jar")):
            plugin_name = os.path.splitext(os.path.basename(jar_file))[0]
            plugins[plugin_name] = {
                "version": "1.0",  # Default version
                "enabled": True,   # Assume enabled
                "path": jar_file
            }

        # Get plugin details from console output if server is running
        if self._is_running:
            # Extract plugin info from console (simplified)
            plugin_pattern = re.compile(r'Loading (.+?) \((.+?)\)')
            for line in self.console_output:
                if 'Loading ' in line and '.jar' in line:
                    match = plugin_pattern.search(line)
                    if match:
                        name, version = match.groups()
                        if name in plugins:
                            plugins[name]["version"] = version

        return plugins

    def enable_plugin(self, plugin_name):
        """Enable a plugin (would require server restart in real implementation)"""
        return True  # Simplified implementation

    def disable_plugin(self, plugin_name):
        """Disable a plugin (would require server restart in real implementation)"""
        return True  # Simplified implementation

    def delete_plugin(self, plugin_name):
        """Delete a plugin jar file"""
        plugins_dir = os.path.join(self.base_dir, "plugins")
        plugin_path = os.path.join(plugins_dir, f"{plugin_name}.jar")

        if os.path.exists(plugin_path):
            try:
                os.remove(plugin_path)
                return True
            except Exception as e:
                print(f"Failed to delete plugin: {str(e)}")

        return False

    # Backup management
    def create_backup(self, backup_name):
        """Create a backup of the server"""
        if not backup_name:
            backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        backups_dir = os.path.join(self.base_dir, "backups")
        if not os.path.exists(backups_dir):
            os.makedirs(backups_dir)

        backup_path = os.path.join(backups_dir, backup_name)

        # Create a zip file of the server directory, excluding backups folder
        try:
            # Create the zip file
            with zipfile.ZipFile(f"{backup_path}.zip", 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Walk through all files and directories in the server directory
                for root, dirs, files in os.walk(self.base_dir):
                    # Skip the backups directory
                    if os.path.basename(root) == "backups" and os.path.dirname(root) == self.base_dir:
                        continue

                    # Add files to zip
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Get relative path (for zip structure)
                        rel_path = os.path.relpath(file_path, self.base_dir)
                        zipf.write(file_path, rel_path)

                    # Skip backup subdirs if we encounter them
                    if "backups" in dirs:
                        dirs.remove("backups")

            return True
        except Exception as e:
            print(f"Backup failed: {str(e)}")
            traceback.print_exc()
            return False

    def get_backups(self):
        """Get list of available backups"""
        backups = []
        backups_dir = os.path.join(self.base_dir, "backups")

        if not os.path.exists(backups_dir):
            return backups

        for backup_file in glob.glob(os.path.join(backups_dir, "*.zip")):
            backup_name = os.path.basename(backup_file).replace('.zip', '')
            try:
                stat = os.stat(backup_file)
                backups.append({
                    "name": backup_name,
                    "path": backup_file,
                    "size": stat.st_size,
                    "timestamp": stat.st_mtime
                })
            except:
                pass

        return backups

    def restore_backup(self, backup_name):
        """Restore server from a backup"""
        if self._is_running:
            raise ValueError("Server must be stopped before restoring a backup")

        backups_dir = os.path.join(self.base_dir, "backups")
        backup_path = os.path.join(backups_dir, f"{backup_name}.zip")

        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"Backup {backup_name} not found")

        try:
            # Create temp dir for extraction
            temp_dir = os.path.join(backups_dir, "temp_restore")
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)

            # Extract backup
            shutil.unpack_archive(backup_path, temp_dir)

            # Remove current server files (except backups)
            for item in os.listdir(self.base_dir):
                if item != "backups":
                    item_path = os.path.join(self.base_dir, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)

            # Copy restored files
            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(self.base_dir, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            # Clean up temp dir
            shutil.rmtree(temp_dir)
            return True
        except Exception as e:
            print(f"Restore failed: {str(e)}")
            raise

    def delete_backup(self, backup_name):
        """Delete a backup"""
        backups_dir = os.path.join(self.base_dir, "backups")
        backup_path = os.path.join(backups_dir, f"{backup_name}.zip")

        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
                return True
            except Exception as e:
                print(f"Failed to delete backup: {str(e)}")

        return False

    def set_backup_schedule(self, enabled, interval, max_backups):
        """Save backup schedule settings"""
        config_file = os.path.join(self.base_dir, "server_config.json")

        config = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
            except:
                pass

        config["backup_schedule"] = {
            "enabled": enabled,
            "interval": interval,
            "max_backups": max_backups
        }

        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to save backup schedule: {str(e)}")
            return False

    def schedule_next_backup(self):
        """Schedule the next backup (this would be implemented differently in production)"""
        pass  # This would be implemented with a scheduler in a real system    def update_settings(self, general=None, world=None, advanced=None):
        """Update server settings"""
        # This is a simplified implementation
        config_file = os.path.join(self.base_dir, "server_config.json")

        config = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
            except:
                pass

        # Update configuration
        if general:
            config["general"] = general

        if world:
            config["world"] = world

        if advanced:
            config["advanced"] = advanced
            if "memory" in advanced:
                self.max_ram = int(advanced["memory"])
            if "java_path" in advanced:
                config["java_path"] = advanced["java_path"]

        # Save configuration
        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)

            # Update server.properties file
            self._update_server_properties(config)
            return True
        except Exception as e:
            print(f"Failed to update settings: {str(e)}")
            return False

    def _update_server_properties(self, config):
        """Update server.properties file with new settings"""
        prop_file = os.path.join(self.base_dir, "server.properties")

        props = {}
        # Read existing properties
        if os.path.exists(prop_file):
            try:
                with open(prop_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            props[key.strip()] = value.strip()
            except:
                pass

        # Update with new values
        if "general" in config:
            for key, value in config["general"].items():
                props[key] = str(value)

        if "world" in config:
            for key, value in config["world"].items():
                props[key] = str(value).lower() if isinstance(value, bool) else str(value)

        if "advanced" in config:
            for key, value in config["advanced"].items():
                if key in ["online-mode", "enable-command-block", "pvp", "force-gamemode", "allow-flight"]:
                    props[key] = str(value).lower() if isinstance(value, bool) else str(value)

        # Write back to file
        try:
            with open(prop_file, 'w') as f:
                for key, value in props.items():
                    f.write(f"{key}={value}\n")
        except Exception as e:
            print(f"Failed to update server.properties: {str(e)}")

    # Optimizations
    def save_optimization_settings(self, settings):
        """Save optimization settings to server config and apply to config files"""
        try:
            # Load current config
            config_path = os.path.join(self.base_dir, "server_config.json")
            with open(config_path, 'r') as f:
                config = json.load(f)

            # Update optimization settings
            config["optimizations"] = settings

            # Save updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            # Apply settings to server configuration files
            self._apply_optimization_settings(settings)

            return True
        except Exception as e:
            print(f"Error saving optimization settings: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _apply_optimization_settings(self, settings):
        """Apply optimization settings to server configuration files using YAML parsing"""
        try:
            import yaml
        except ImportError:
            print("PyYAML is required. Please install it with: pip install pyyaml")
            return False

        # Apply vanilla settings to server.properties (regular properties file)
        if "vanilla" in settings:
            props_file = os.path.join(self.base_dir, "server.properties")
            if os.path.exists(props_file):
                # Properties files aren't YAML, so use the existing approach
                with open(props_file, 'r') as f:
                    content = f.read()

                for key, value in settings["vanilla"].items():
                    pattern = re.compile(f"{key}=.*(\r?\n|$)")
                    replacement = f"{key}={value}\\1"
                    content = re.sub(pattern, replacement, content)

                with open(props_file, 'w') as f:
                    f.write(content)

        # Apply bukkit settings to bukkit.yml using YAML parser
        if "bukkit" in settings:
            bukkit_file = os.path.join(self.base_dir, "bukkit.yml")
            config = {}

            # Load existing config if file exists
            if os.path.exists(bukkit_file):
                with open(bukkit_file, 'r') as f:
                    config = yaml.safe_load(f) or {}

            # Ensure required sections exist
            if 'chunk-gc' not in config:
                config['chunk-gc'] = {}
            if 'spawn-limits' not in config:
                config['spawn-limits'] = {}

            # Apply settings
            if "period-in-ticks" in settings["bukkit"]:
                config['chunk-gc']['period-in-ticks'] = int(settings["bukkit"]["period-in-ticks"])

            if "monsters" in settings["bukkit"]:
                config['spawn-limits']['monsters'] = int(settings["bukkit"]["monsters"])

            # Write back the updated config
            with open(bukkit_file, 'w') as f:
                yaml.safe_dump(config, f, default_flow_style=False)

        # Apply preset settings using YAML parser
        if "presets" in settings:
            for preset_key, applied in settings["presets"].items():
                if applied:
                    parts = preset_key.split('_', 1)
                    if len(parts) == 2:
                        file_type, preset_name = parts

                        # Get preset options if available
                        preset_options = None
                        if "preset_data" in settings and preset_key in settings["preset_data"]:
                            preset_options = settings["preset_data"][preset_key]

                        self._apply_preset(file_type, preset_name, preset_options)

        return True

    def _apply_preset(self, file_type, preset_name, preset_options=None):
        """Apply a specific preset to the config file using YAML parsing"""
        try:
            import yaml
        except ImportError:
            print("PyYAML is required. Please install it with: pip install pyyaml")
            return

        if file_type == "spigot":
            spigot_file = os.path.join(self.base_dir, "spigot.yml")
            config = {}

            # Load existing config if file exists
            if os.path.exists(spigot_file):
                with open(spigot_file, 'r') as f:
                    config = yaml.safe_load(f) or {}

            # Ensure required structure exists
            if 'world-settings' not in config:
                config['world-settings'] = {}
            if 'default' not in config['world-settings']:
                config['world-settings']['default'] = {}

            default_section = config['world-settings']['default']

            # Apply preset based on name and type
            if "item_xp_merge_radius" in preset_name:
                # Setup merge-radius section
                if 'merge-radius' not in default_section:
                    default_section['merge-radius'] = {}

                # Apply values from preset options or use defaults
                if preset_options:
                    for setting_key, (default_val, optimized_val) in preset_options.items():
                        key = setting_key.strip()
                        if key.endswith(':'):
                            key = key[:-1]  # Remove trailing colon

                        if key == 'item' or key == 'exp':
                            default_section['merge-radius'][key] = float(optimized_val)
                else:
                    # Use hardcoded defaults if no options provided
                    default_section['merge-radius']['item'] = 1.0
                    default_section['merge-radius']['exp'] = 1.0

            elif "entity_tracking_range" in preset_name:
                # Setup entity-tracking-range section
                if 'entity-tracking-range' not in default_section:
                    default_section['entity-tracking-range'] = {}

                # Apply values from preset options or use defaults
                if preset_options:
                    for setting_key, (default_val, optimized_val) in preset_options.items():
                        key = setting_key.strip()
                        if key.endswith(':'):
                            key = key[:-1]  # Remove trailing colon

                        if key in ['players', 'animals', 'monsters', 'misc', 'other']:
                            default_section['entity-tracking-range'][key] = int(optimized_val)
                else:
                    # Use hardcoded defaults
                    default_section['entity-tracking-range']['players'] = 48
                    default_section['entity-tracking-range']['animals'] = 48
                    default_section['entity-tracking-range']['monsters'] = 48
                    default_section['entity-tracking-range']['misc'] = 32
                    default_section['entity-tracking-range']['other'] = 32

            elif "entity_activation_range" in preset_name:
                # Setup entity-activation-range section
                if 'entity-activation-range' not in default_section:
                    default_section['entity-activation-range'] = {}

                # Apply values from preset options or use defaults
                if preset_options:
                    for setting_key, (default_val, optimized_val) in preset_options.items():
                        key = setting_key.strip()
                        if key.endswith(':'):
                            key = key[:-1]  # Remove trailing colon

                        if key in ['animals', 'monsters', 'raiders', 'misc',
                                  'water', 'villagers', 'flying-monsters']:
                            default_section['entity-activation-range'][key] = int(optimized_val)
                else:
                    # Use hardcoded defaults
                    default_section['entity-activation-range']['animals'] = 16
                    default_section['entity-activation-range']['monsters'] = 24
                    default_section['entity-activation-range']['raiders'] = 48
                    default_section['entity-activation-range']['misc'] = 8
                    default_section['entity-activation-range']['water'] = 8
                    default_section['entity-activation-range']['villagers'] = 16
                    default_section['entity-activation-range']['flying-monsters'] = 24

            # Write the updated config back to the file
            with open(spigot_file, 'w') as f:
                yaml.safe_dump(config, f, default_flow_style=False)

    def get_optimization_settings(self):
        """Get current optimization settings from server config"""
        config_path = os.path.join(self.base_dir, "server_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)

                if "optimizations" in config:
                    return config["optimizations"]
            except:
                pass

        return {
            "vanilla": {
                "simulation-distance": "10",
                "entity-broadcast-range-percentage": "100"
            },
            "bukkit": {
                "period-in-ticks": "600",
                "monsters": "70"
            },
            "presets": {}
        }

    def _find_java_executable(self):
        """Find the Java executable path on the system"""
        # Check if a custom Java path is specified in the server config
        config_file = os.path.join(self.base_dir, "server_config.json")
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    if "java_path" in config and os.path.exists(config["java_path"]):
                        return config["java_path"]
            except Exception as e:
                print(f"Error reading server config: {str(e)}")

        # Try the system PATH first
        try:
            # Use where command on Windows to find java executable
            result = subprocess.run(["where", "java"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split('\n')[0]
        except Exception:
            pass
              # Common Java installation directories on Windows
        possible_locations = [
            # System-wide installations (admin required)
            os.path.join(os.environ.get('PROGRAMFILES', r'C:\Program Files'), 'Java'),
            os.path.join(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'), 'Java'),
            r'C:\Program Files\Eclipse Adoptium',
            r'C:\Program Files\Eclipse Foundation',
            r'C:\Program Files\AdoptOpenJDK',
            r'C:\Program Files\BellSoft\LibericaJDK',

            # User-specific installations (no admin required)
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Java'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'AdoptOpenJDK'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Eclipse Adoptium'),
            os.path.join(os.environ.get('APPDATA', ''), 'Java'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'Java'),

            # Look in the servers directory for a portable Java installation
            os.path.join(os.path.dirname(os.path.dirname(self.base_dir)), 'java'),
            os.path.join(self.base_dir, 'java'),

            # Look in common download locations
            os.path.join(os.environ.get('USERPROFILE', ''), 'Downloads', 'Java'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'Downloads')
        ]

        for base_dir in possible_locations:
            if os.path.exists(base_dir):
                # Search for java.exe in subdirectories
                for root, dirs, files in os.walk(base_dir):
                    if 'bin' in dirs:
                        java_exe = os.path.join(root, 'bin', 'java.exe')
                        if os.path.exists(java_exe):
                            return java_exe

        return None





