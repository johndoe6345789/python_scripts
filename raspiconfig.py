#!/usr/bin/env python3
"""
Raspbian SD Card Configurator with Curses TUI
Interactive configuration for raspi-config, WiFi, and user passwords
"""

import curses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Callable
import crypt
from enum import Enum


@dataclass
class WifiConfig:
    ssid: str = ""
    password: str = ""
    country: str = "US"
    key_mgmt: str = "WPA-PSK"


@dataclass
class UserConfig:
    username: str = ""
    password: str = ""
    enable_sudo: bool = True


@dataclass
class RaspianConfig:
    hostname: str = ""
    enable_ssh: bool = True
    gpu_mem: Optional[int] = None
    overclock: Optional[str] = None
    wifi: WifiConfig = field(default_factory=WifiConfig)
    users: List[UserConfig] = field(default_factory=list)


class InputMode(Enum):
    NORMAL = 1
    INPUT = 2
    CONFIRM = 3


class RaspianConfiguratorTUI:
    def __init__(self, mount_path: str):
        self.mount_path = Path(mount_path)
        self.boot_path = self.mount_path / "boot"
        self.rootfs_path = self.mount_path / "rootfs"
        
        self.config = RaspianConfig()
        self.mode = InputMode.NORMAL
        self.current_field = 0
        self.input_buffer = ""
        self.selected_user = 0
        self.message = ""
        self.message_type = "info"  # "info", "error", "success"
        
        self.fields = [
            ("Hostname", "hostname", str),
            ("Enable SSH", "enable_ssh", bool),
            ("GPU Memory (MB)", "gpu_mem", int),
            ("Overclock", "overclock", str),
            ("WiFi SSID", "wifi.ssid", str),
            ("WiFi Password", "wifi.password", str),
            ("WiFi Country", "wifi.country", str),
            ("Pi Username", "user_username", str),
            ("Pi Password", "user_password", str),
        ]
    
    def get_field_value(self, field_name: str):
        """Get value from config using dot notation."""
        if '.' in field_name:
            obj_name, attr_name = field_name.split('.')
            obj = getattr(self.config, obj_name)
            return getattr(obj, attr_name)
        else:
            return getattr(self.config, field_name)
    
    def set_field_value(self, field_name: str, value):
        """Set value in config using dot notation."""
        if '.' in field_name:
            obj_name, attr_name = field_name.split('.')
            obj = getattr(self.config, obj_name)
            setattr(obj, attr_name, value)
        else:
            setattr(self.config, field_name, value)
    
    def validate_mount_path(self) -> bool:
        """Check if mount path is valid."""
        if not self.boot_path.exists() or not self.rootfs_path.exists():
            self.set_message(
                f"Invalid mount path. Need 'boot' and 'rootfs' directories",
                "error"
            )
            return False
        return True
    
    def set_message(self, msg: str, msg_type: str = "info"):
        """Set status message."""
        self.message = msg
        self.message_type = msg_type
    
    def apply_configuration(self) -> bool:
        """Apply all configuration to SD card."""
        try:
            if self.config.hostname:
                self._set_hostname(self.config.hostname)
            
            if self.config.enable_ssh:
                self._enable_ssh()
            
            if self.config.gpu_mem:
                self._set_gpu_memory(self.config.gpu_mem)
            
            if self.config.overclock:
                self._set_overclock(self.config.overclock)
            
            if self.config.wifi.ssid:
                self._configure_wifi(self.config.wifi)
            
            if self.config.users:
                for user in self.config.users:
                    if user.username and user.password:
                        self._configure_user(user)
            
            self.set_message("Configuration applied successfully!", "success")
            return True
        except Exception as e:
            self.set_message(f"Error: {str(e)}", "error")
            return False
    
    def _set_hostname(self, hostname: str) -> None:
        hostname_file = self.rootfs_path / "etc" / "hostname"
        hostname_file.write_text(f"{hostname}\n")
        
        hosts_file = self.rootfs_path / "etc" / "hosts"
        content = hosts_file.read_text()
        content = content.replace("raspberrypi", hostname)
        hosts_file.write_text(content)
    
    def _enable_ssh(self) -> None:
        ssh_file = self.boot_path / "ssh"
        ssh_file.touch()
    
    def _set_gpu_memory(self, gpu_mem: int) -> None:
        config_file = self.boot_path / "config.txt"
        content = config_file.read_text()
        lines = [line for line in content.split('\n') if not line.startswith('gpu_mem')]
        lines.append(f"gpu_mem={gpu_mem}")
        config_file.write_text('\n'.join(lines))
    
    def _set_overclock(self, preset: str) -> None:
        presets = {
            'modest': {'arm_freq': 1900, 'gpu_freq': 600, 'over_voltage': 2},
            'medium': {'arm_freq': 2000, 'gpu_freq': 650, 'over_voltage': 4},
            'high': {'arm_freq': 2147, 'gpu_freq': 750, 'over_voltage': 6},
        }
        
        if preset not in presets:
            raise ValueError(f"Unknown preset: {preset}")
        
        config_file = self.boot_path / "config.txt"
        content = config_file.read_text()
        lines = [
            line for line in content.split('\n')
            if not any(key in line for key in presets[preset].keys())
        ]
        
        for key, value in presets[preset].items():
            lines.append(f"{key}={value}")
        
        config_file.write_text('\n'.join(lines))
    
    def _configure_wifi(self, wifi: WifiConfig) -> None:
        wpa_supplicant_file = self.rootfs_path / "etc" / "wpa_supplicant" / "wpa_supplicant.conf"
        content = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country={wifi.country}

network={{
    ssid="{wifi.ssid}"
    psk="{wifi.password}"
    key_mgmt={wifi.key_mgmt}
}}
"""
        wpa_supplicant_file.write_text(content)
    
    def _configure_user(self, user: UserConfig) -> None:
        shadow_file = self.rootfs_path / "etc" / "shadow"
        
        if not shadow_file.exists():
            raise FileNotFoundError(f"Shadow file not found")
        
        password_hash = crypt.crypt(user.password, crypt.METHOD_SHA512)
        
        content = shadow_file.read_text()
        lines = content.split('\n')
        
        user_found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{user.username}:"):
                parts = line.split(':')
                parts[1] = password_hash
                lines[i] = ':'.join(parts)
                user_found = True
                break
        
        if not user_found:
            raise ValueError(f"User '{user.username}' not found")
        
        shadow_file.write_text('\n'.join(lines))
        shadow_file.chmod(0o000)
    
    def run(self, stdscr):
        """Main TUI loop."""
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(100)
        
        # Color pairs
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)    # Error
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Success
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)   # Info
        
        if not self.validate_mount_path():
            stdscr.clear()
            stdscr.addstr(0, 0, self.message, curses.color_pair(2))
            stdscr.refresh()
            stdscr.getch()
            return
        
        while True:
            stdscr.clear()
            height, width = stdscr.getmaxyx()
            
            # Header
            stdscr.addstr(0, 0, "Raspbian SD Card Configurator", curses.A_BOLD)
            stdscr.addstr(1, 0, "─" * width)
            
            # Status message
            if self.message:
                color_pair = {
                    "error": 2,
                    "success": 3,
                    "info": 4
                }.get(self.message_type, 4)
                stdscr.addstr(2, 0, self.message, curses.color_pair(color_pair))
                self.message = ""
            
            # Fields display
            y = 4
            for i, (label, field_name, field_type) in enumerate(self.fields):
                value = self.get_field_value(field_name)
                
                # Format value for display
                if isinstance(value, bool):
                    display_value = "Yes" if value else "No"
                elif value is None:
                    display_value = "(empty)"
                else:
                    display_value = str(value)
                
                # Highlight selected field
                if i == self.current_field:
                    stdscr.addstr(y, 0, f"> {label}: {display_value}", curses.color_pair(1))
                else:
                    stdscr.addstr(y, 0, f"  {label}: {display_value}")
                
                y += 1
                if y >= height - 4:
                    break
            
            # Footer
            stdscr.addstr(height - 3, 0, "─" * width)
            stdscr.addstr(height - 2, 0, 
                         "↑/↓: Navigate | Enter: Edit | Space: Toggle | Ctrl+S: Save | Ctrl+Q: Quit")
            stdscr.addstr(height - 1, 0, f"Mount: {self.mount_path}")
            
            stdscr.refresh()
            
            # Input handling
            try:
                key = stdscr.getch()
                
                if key == -1:  # No input
                    continue
                
                elif key == ord('q') or key == 3:  # Ctrl+C
                    break
                
                elif key == ord('s') or key == 19:  # Ctrl+S
                    if self.apply_configuration():
                        stdscr.getch()  # Wait for user to see success
                
                elif key == curses.KEY_UP:
                    self.current_field = max(0, self.current_field - 1)
                
                elif key == curses.KEY_DOWN:
                    self.current_field = min(len(self.fields) - 1, self.current_field + 1)
                
                elif key == ord('\n'):  # Enter
                    self._enter_edit_mode(stdscr)
                
                elif key == ord(' '):  # Space - toggle boolean
                    field_name = self.fields[self.current_field][1]
                    field_type = self.fields[self.current_field][2]
                    if field_type == bool:
                        current = self.get_field_value(field_name)
                        self.set_field_value(field_name, not current)
            
            except KeyboardInterrupt:
                break
    
    def _enter_edit_mode(self, stdscr):
        """Enter edit mode for current field."""
        label, field_name, field_type = self.fields[self.current_field]
        current_value = self.get_field_value(field_name)
        
        height, width = stdscr.getmaxyx()
        input_y = height - 4
        
        curses.curs_set(1)
        self.input_buffer = str(current_value) if current_value else ""
        
        while True:
            stdscr.addstr(input_y, 0, " " * width)
            prompt = f"{label}: "
            stdscr.addstr(input_y, 0, prompt + self.input_buffer)
            stdscr.refresh()
            
            try:
                key = stdscr.getch()
                
                if key == ord('\n'):  # Confirm
                    try:
                        if field_type == bool:
                            value = self.input_buffer.lower() in ('y', 'yes', '1', 'true')
                        elif field_type == int:
                            value = int(self.input_buffer) if self.input_buffer else None
                        else:
                            value = self.input_buffer
                        
                        self.set_field_value(field_name, value)
                        self.set_message(f"{label} updated", "success")
                    except ValueError:
                        self.set_message(f"Invalid {field_type.__name__} value", "error")
                    break
                
                elif key == 27:  # ESC - cancel
                    break
                
                elif key == curses.KEY_BACKSPACE or key == 127:
                    self.input_buffer = self.input_buffer[:-1]
                
                elif 32 <= key <= 126:  # Printable characters
                    self.input_buffer += chr(key)
            
            except KeyboardInterrupt:
                break
        
        curses.curs_set(0)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Raspbian SD Card Configurator (TUI)")
    parser.add_argument("mount_path", help="Path to mounted Raspbian SD card root")
    args = parser.parse_args()
    
    tui = RaspianConfiguratorTUI(args.mount_path)
    curses.wrapper(tui.run)


if __name__ == "__main__":
    main()
