#!/usr/bin/env python3
"""
Raspbian SD Card Configurator - n8n-style Node Workflow Engine
"""

import curses
import json
import sys
import re
import crypt
from pathlib import Path
from typing import Any, Dict


class WorkflowEngine:
    """Execute n8n-style node workflows."""
    
    def __init__(self, schema_path: str, mount_path: str):
        with open(schema_path) as f:
            self.schema = json.load(f)
        
        self.mount_path = Path(mount_path)
        self.vars = {}
        self.node_outputs = {}  # Store outputs: {node_id: {output_name: value}}
        self.mounts = {
            name: str(self.mount_path / rel_path)
            for name, rel_path in self.schema["mounts"].items()
        }
        
        # Action dispatch table
        self.actions = {
            "file.write": self._file_write,
            "file.read": self._file_read,
            "file.touch": self._file_touch,
            "text.replace": self._text_replace,
            "text.filter_lines": self._text_filter_lines,
            "text.append": self._text_append,
            "text.template": self._text_template,
            "text.sed": self._text_sed,
            "crypto.sha512": self._crypto_sha512,
            "preset.apply": self._preset_apply,
        }
    
    def substitute(self, text: str) -> str:
        """Substitute {var}, {mount.name}, and {{node.output}}."""
        result = text
        
        # Substitute variables
        for key, val in self.vars.items():
            result = result.replace(f"{{{key}}}", str(val) if val else "")
        
        # Substitute mounts
        for name, path in self.mounts.items():
            result = result.replace(f"{{mount.{name}}}", path)
        
        # Substitute node outputs: {{node_id.output}}
        for node_id, outputs in self.node_outputs.items():
            for output_name, value in outputs.items():
                result = result.replace(f"{{{{{node_id}.{output_name}}}}}", str(value) if value else "")
        
        return result
    
    def execute(self, field_values: Dict[str, Any]) -> tuple[bool, str]:
        """Execute nodes following connection graph order."""
        try:
            self.vars.update(field_values)
            
            nodes_list = self.schema["nodes"]
            nodes_by_id = {node["id"]: node for node in nodes_list}
            connections = self.schema.get("connections", {})
            
            # Build reverse dependency graph: node -> nodes that depend on it
            # and forward dependency graph: node -> nodes it depends on
            depends_on = {}  # node_id -> [node_ids it needs]
            depended_by = {}  # node_id -> [node_ids that need it]
            
            for node in nodes_list:
                depends_on[node["id"]] = []
                depended_by[node["id"]] = []
            
            # Parse connections: from_node.output -> to_node.input
            for from_node_id, conn_map in connections.items():
                if from_node_id not in depends_on:
                    depends_on[from_node_id] = []
                    depended_by[from_node_id] = []
                
                for output_type, output_idx_map in conn_map.items():
                    for output_idx, targets in output_idx_map.items():
                        for target in targets:
                            to_node_id = target["node"]
                            if to_node_id not in depends_on:
                                depends_on[to_node_id] = []
                                depended_by[to_node_id] = []
                            
                            depends_on[to_node_id].append(from_node_id)
                            depended_by[from_node_id].append(to_node_id)
            
            # Find root nodes (no dependencies)
            root_nodes = [n["id"] for n in nodes_list if not depends_on[n["id"]]]
            
            executed = set()
            execution_order = []
            
            def topological_sort(node_id: str):
                """Depth-first traversal to build execution order."""
                if node_id in executed:
                    return
                
                # Execute dependencies first
                for dep in depends_on.get(node_id, []):
                    topological_sort(dep)
                
                execution_order.append(node_id)
                executed.add(node_id)
            
            # Build execution order from roots
            for root in root_nodes:
                topological_sort(root)
            
            # Also traverse from non-root nodes that aren't reached
            for node in nodes_list:
                topological_sort(node["id"])
            
            # Now execute in order
            for node_id in execution_order:
                node = nodes_by_id[node_id]
                params = node.get("parameters", {})
                
                # Check condition
                if "condition" in params:
                    if not self._eval_condition(params["condition"]):
                        continue
                
                # Execute node
                action = node.get("type")
                if action not in self.actions:
                    raise ValueError(f"Unknown action: {action}")
                
                # Resolve parameters with substitution (includes node outputs)
                resolved_params = {
                    k: self.substitute(str(v)) 
                    for k, v in params.items() 
                    if k != "condition"
                }
                
                # Execute action
                outputs = self.actions[action](resolved_params)
                
                # Store outputs under node.data.outputname format
                if outputs:
                    self.node_outputs[f"{node_id}.data"] = outputs
            
            meta = self.schema.get("meta", {})
            msg = meta.get("strings", {}).get("apply_success", "Success")
            return True, msg
        
        except Exception as e:
            meta = self.schema.get("meta", {})
            msg_template = meta.get("strings", {}).get("apply_error", "Error: {error}")
            return False, msg_template.format(error=str(e))
    
    def _eval_condition(self, condition: str) -> bool:
        """Evaluate boolean condition."""
        condition = self.substitute(condition)
        try:
            return bool(eval(condition, {"__builtins__": {}}, {}))
        except:
            return False
    
    # Action handlers - return dict of outputs
    def _file_write(self, params: Dict) -> Dict:
        path = Path(params["path"])
        content = params["content"]
        path.write_text(content)
        if "chmod" in params:
            path.chmod(int(params["chmod"], 8))
        return {}
    
    def _file_read(self, params: Dict) -> Dict:
        path = Path(params["path"])
        return {"content": path.read_text()}
    
    def _file_touch(self, params: Dict) -> Dict:
        Path(params["path"]).touch()
        return {}
    
    def _text_replace(self, params: Dict) -> Dict:
        text = params["text"]
        result = text.replace(params["find"], params["replace"])
        return {"result": result}
    
    def _text_filter_lines(self, params: Dict) -> Dict:
        text = params["text"]
        lines = text.split('\n')
        for pattern in params.get("exclude", []):
            lines = [l for l in lines if not re.match(pattern, l)]
        return {"result": '\n'.join(lines)}
    
    def _text_append(self, params: Dict) -> Dict:
        text = params["text"]
        line = params["line"]
        result = (text + '\n' + line) if text else line
        return {"result": result}
    
    def _text_template(self, params: Dict) -> Dict:
        template = self.schema["templates"][params["template"]]
        result = template
        for key, val in params.get("vars", {}).items():
            result = result.replace(f"{{{key}}}", val)
        return {"result": result}
    
    def _text_sed(self, params: Dict) -> Dict:
        text = params["text"]
        pattern = params["pattern"]
        replacement = params["replacement"]
        lines = text.split('\n')
        result_lines = [
            re.sub(pattern, replacement, line) if re.search(pattern, line) else line
            for line in lines
        ]
        return {"result": '\n'.join(result_lines)}
    
    def _crypto_sha512(self, params: Dict) -> Dict:
        password = params["password"]
        return {"hash": crypt.crypt(password, crypt.METHOD_SHA512)}
    
    def _preset_apply(self, params: Dict) -> Dict:
        text = params["text"]
        preset_name = params["preset"]
        preset_data = self.schema["presets"][preset_name]
        lines = text.split('\n') if text else []
        for key, val in preset_data.items():
            lines.append(f"{key}={val}")
        return {"result": '\n'.join(lines)}


class WorkflowGraphBuilder:
    """Build a visual representation of the workflow."""
    
    @staticmethod
    def find_dependencies(nodes_list: list, connections: dict) -> dict:
        """Find which nodes depend on which (from n8n connections)."""
        node_deps = {}
        for node in nodes_list:
            node_deps[node["id"]] = []
        
        for from_node, conn_map in connections.items():
            for output_type, output_idx_map in conn_map.items():
                for output_idx, targets in output_idx_map.items():
                    for target in targets:
                        to_node = target["node"]
                        if to_node not in node_deps:
                            node_deps[to_node] = []
                        node_deps[to_node].append(from_node)
        
        return node_deps
    
    @staticmethod
    def draw_graph(stdscr, nodes_list: list, connections: dict, y_start: int):
        """Draw workflow graph visualization."""
        h, w = stdscr.getmaxyx()
        node_deps = WorkflowGraphBuilder.find_dependencies(nodes_list, connections)
        
        y = y_start
        for node in nodes_list:
            if y >= h - 3:
                stdscr.addstr(y, 0, "  ... (more nodes)")
                break
            
            node_id = node["id"]
            node_type = node.get("type", "unknown")
            params = node.get("parameters", {})
            condition = f" [if: {params.get('condition', 'always')}]" if "condition" in params else ""
            
            line = f"  ◆ {node_id}: {node_type}{condition}"[:w-2]
            stdscr.addstr(y, 0, line)
            y += 1
            
            deps = node_deps.get(node_id, [])
            if deps:
                for dep in deps:
                    line = f"    ← {dep}"[:w-2]
                    stdscr.addstr(y, 0, line)
                    y += 1


class RaspianConfiguratorTUI:
    """Curses TUI."""
    
    def __init__(self, schema_path: str, mount_path: str):
        with open(schema_path) as f:
            self.schema = json.load(f)
        
        self.engine = WorkflowEngine(schema_path, mount_path)
        self.config = {}
        self.current_field = 0
        self.input_buffer = ""
        self.message = ""
        self.message_type = "info"
        self.view_mode = "config"  # "config" or "workflow"
        
        # Get fields from meta (n8n style)
        meta = self.schema.get("meta", {})
        fields = meta.get("fields", [])
        for field in fields:
            self.config[field["id"]] = field.get("default")
    
    def validate_mount(self) -> bool:
        meta = self.schema.get("meta", {})
        mounts = meta.get("mounts", {})
        boot = self.engine.mount_path / mounts.get("boot", "boot")
        rootfs = self.engine.mount_path / mounts.get("rootfs", "rootfs")
        if not boot.exists() or not rootfs.exists():
            self.message = self.schema["strings"]["invalid_mount"]
            self.message_type = "error"
            return False
        return True
    
    def validate(self, field: Dict, value: Any) -> tuple[bool, str]:
        for rule in field.get("validate", []):
            if rule["type"] == "pattern":
                if not re.match(rule["pattern"], str(value)):
                    return False, "Invalid format"
            elif rule["type"] == "length":
                if len(str(value)) < rule["min"]:
                    return False, "Too short"
        return True, ""
    
    def apply(self) -> bool:
        success, message = self.engine.execute(self.config)
        self.message = message
        self.message_type = "success" if success else "error"
        return success
    
    def run(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(100)
        
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
        
        if not self.validate_mount():
            stdscr.clear()
            stdscr.addstr(0, 0, self.message, curses.color_pair(2))
            stdscr.refresh()
            stdscr.getch()
            return
        
        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            
            title = self.schema["metadata"]["name"]
            mode_label = " [WORKFLOW]" if self.view_mode == "workflow" else " [CONFIG]"
            stdscr.addstr(0, 0, title + mode_label, curses.A_BOLD)
            stdscr.addstr(1, 0, "─" * w)
            
            if self.message:
                color = {"error": 2, "success": 3, "info": 4}.get(self.message_type, 4)
                stdscr.addstr(2, 0, self.message[:w-1], curses.color_pair(color))
                self.message = ""
            
            if self.view_mode == "config":
                meta = self.schema.get("meta", {})
                fields = meta.get("fields", [])
                y = 4
                for i, field in enumerate(fields):
                    if y >= h - 4:
                        break
                    fid = field["id"]
                    val = self.config.get(fid)
                    
                    if field["type"] == "bool":
                        disp = "Yes" if val else "No"
                    elif field["type"] == "password":
                        disp = "●" * min(len(str(val)), 8) if val else "(empty)"
                    elif val is None or val == "":
                        disp = "(empty)"
                    else:
                        disp = str(val)
                    
                    line = f"> {field['label']}: {disp}" if i == self.current_field else f"  {field['label']}: {disp}"
                    color = curses.color_pair(1) if i == self.current_field else 0
                    stdscr.addstr(y, 0, line, color)
                    y += 1
            else:
                stdscr.addstr(3, 0, "Workflow Graph:", curses.A_DIM)
                WorkflowGraphBuilder.draw_graph(stdscr, self.schema["nodes"], self.schema.get("connections", {}), 4)
            
            stdscr.addstr(h - 3, 0, "─" * w)
            footer = "Tab: Toggle View | " + self.schema["strings"]["nav_help"]
            stdscr.addstr(h - 2, 0, footer[:w-1])
            stdscr.addstr(h - 1, 0, f"Mount: {self.engine.mount_path}")
            stdscr.refresh()
            
            try:
                key = stdscr.getch()
                if key == -1:
                    continue
                elif key == ord('q') or key == 3:
                    break
                elif key == ord('\t'):
                    self.view_mode = "workflow" if self.view_mode == "config" else "config"
                elif key == ord('s') or key == 19:
                    self.apply()
                    stdscr.getch()
                elif self.view_mode == "config":
                    meta = self.schema.get("meta", {})
                    fields = meta.get("fields", [])
                    if key == curses.KEY_UP:
                        self.current_field = max(0, self.current_field - 1)
                    elif key == curses.KEY_DOWN:
                        self.current_field = min(len(fields) - 1, self.current_field + 1)
                    elif key == ord('\n'):
                        self._edit_field(stdscr)
                    elif key == ord(' '):
                        field = fields[self.current_field]
                        if field["type"] == "bool":
                            self.config[field["id"]] = not self.config[field["id"]]
            except KeyboardInterrupt:
                break
    
    def _edit_field(self, stdscr):
        meta = self.schema.get("meta", {})
        fields = meta.get("fields", [])
        field = fields[self.current_field]
        fid = field["id"]
        h, w = stdscr.getmaxyx()
        iy = h - 4
        
        curses.curs_set(1)
        self.input_buffer = str(self.config.get(fid, "")) if self.config.get(fid) else ""
        
        while True:
            stdscr.addstr(iy, 0, " " * w)
            prompt = f"{field['label']}: "
            disp = "●" * len(self.input_buffer) if field["type"] == "password" else self.input_buffer
            stdscr.addstr(iy, 0, prompt + disp)
            stdscr.refresh()
            
            try:
                key = stdscr.getch()
                if key == ord('\n'):
                    try:
                        if field["type"] == "bool":
                            val = self.input_buffer.lower() in ('y', 'yes', '1', 'true')
                        elif field["type"] == "int":
                            val = int(self.input_buffer) if self.input_buffer else None
                        else:
                            val = self.input_buffer
                        
                        ok, err = self.validate(field, val)
                        if not ok:
                            self.message = err
                            self.message_type = "error"
                        else:
                            self.config[fid] = val
                            self.message = f"{field['label']} updated"
                            self.message_type = "success"
                    except ValueError:
                        self.message = f"Invalid {field['type']}"
                        self.message_type = "error"
                    break
                elif key == 27:
                    break
                elif key == curses.KEY_BACKSPACE or key == 127:
                    self.input_buffer = self.input_buffer[:-1]
                elif 32 <= key <= 126:
                    self.input_buffer += chr(key)
            except KeyboardInterrupt:
                break
        
        curses.curs_set(0)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mount_path")
    parser.add_argument("-c", "--config", default="config.json")
    args = parser.parse_args()
    
    tui = RaspianConfiguratorTUI(args.config, args.mount_path)
    curses.wrapper(tui.run)


if __name__ == "__main__":
    main()
