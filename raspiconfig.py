#!/usr/bin/env python3
"""
Raspbian SD Card Configurator - Flask Web App
n8n-style workflow engine with web UI
"""

from flask import Flask, render_template, request, jsonify
from pathlib import Path
import json
import re
import crypt
from typing import Any, Dict, Tuple

app = Flask(__name__)


class WorkflowEngine:
    """Execute nodes following n8n connection graph."""
    
    def __init__(self, schema_path: str, mount_path: str):
        with open(schema_path) as f:
            self.schema = json.load(f)
        
        self.mount_path = Path(mount_path)
        self.vars = {}
        self.node_outputs = {}
        meta = self.schema.get("meta", {})
        mounts = meta.get("mounts", {})
        self.mounts = {
            name: str(self.mount_path / rel_path)
            for name, rel_path in mounts.items()
        }
        
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
            "noop": self._noop,
        }
    
    def substitute(self, text: str) -> str:
        """Substitute variables and node outputs."""
        result = text
        for key, val in self.vars.items():
            result = result.replace(f"{{{key}}}", str(val) if val else "")
        for name, path in self.mounts.items():
            result = result.replace(f"{{mount.{name}}}", path)
        for node_id, outputs in self.node_outputs.items():
            for output_name, value in outputs.items():
                result = result.replace(f"{{{{{node_id}.data.{output_name}}}}}", str(value) if value else "")
        return result
    
    def execute(self, field_values: Dict[str, Any]) -> Tuple[bool, str]:
        """Execute workflow respecting connection graph."""
        try:
            self.vars.update(field_values)
            
            nodes_list = self.schema["nodes"]
            nodes_by_id = {node["id"]: node for node in nodes_list}
            connections = self.schema.get("connections", {})
            
            # Build dependency graph
            depends_on = {}
            for node in nodes_list:
                depends_on[node["id"]] = []
            
            for from_node_id, conn_map in connections.items():
                if from_node_id not in depends_on:
                    depends_on[from_node_id] = []
                
                for output_type, output_idx_map in conn_map.items():
                    for output_idx, targets in output_idx_map.items():
                        for target in targets:
                            to_node_id = target["node"]
                            if to_node_id not in depends_on:
                                depends_on[to_node_id] = []
                            depends_on[to_node_id].append(from_node_id)
            
            # Topological sort
            root_nodes = [n["id"] for n in nodes_list if not depends_on[n["id"]]]
            executed = set()
            execution_order = []
            
            def topological_sort(node_id: str):
                if node_id in executed:
                    return
                for dep in depends_on.get(node_id, []):
                    topological_sort(dep)
                execution_order.append(node_id)
                executed.add(node_id)
            
            for root in root_nodes:
                topological_sort(root)
            
            for node in nodes_list:
                topological_sort(node["id"])
            
            # Execute in order
            for node_id in execution_order:
                node = nodes_by_id[node_id]
                params = node.get("parameters", {})
                
                if "condition" in params:
                    if not self._eval_condition(params["condition"]):
                        continue
                
                action = node.get("type")
                if action not in self.actions:
                    raise ValueError(f"Unknown action: {action}")
                
                resolved_params = {
                    k: self.substitute(str(v))
                    for k, v in params.items()
                    if k != "condition"
                }
                
                outputs = self.actions[action](resolved_params)
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
        condition = self.substitute(condition)
        try:
            return bool(eval(condition, {"__builtins__": {}}, {}))
        except:
            return False
    
    # Action handlers
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
        meta = self.schema.get("meta", {})
        templates = meta.get("templates", {})
        template = templates[params["template"]]
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
        meta = self.schema.get("meta", {})
        preset_data = meta.get("presets", {})[preset_name]
        lines = text.split('\n') if text else []
        for key, val in preset_data.items():
            lines.append(f"{key}={val}")
        return {"result": '\n'.join(lines)}
    
    def _noop(self, params: Dict) -> Dict:
        return {}


# Global engine instance
engine = None


@app.route('/')
def index():
    """Render main page."""
    with open('config.json') as f:
        schema = json.load(f)
    
    meta = schema.get("meta", {})
    fields = meta.get("fields", [])
    
    return render_template('index.html', fields=fields, title=schema.get("name", "Configurator"))


@app.route('/api/workflow')
def get_workflow():
    """Get workflow graph data."""
    with open('config.json') as f:
        schema = json.load(f)
    
    nodes = schema.get("nodes", [])
    connections = schema.get("connections", {})
    
    return jsonify({
        "nodes": nodes,
        "connections": connections
    })


@app.route('/api/apply', methods=['POST'])
def apply_config():
    """Apply configuration via workflow."""
    data = request.get_json()
    
    if not engine:
        return jsonify({"success": False, "message": "Engine not initialized"}), 500
    
    success, message = engine.execute(data)
    return jsonify({
        "success": success,
        "message": message
    })


def init_engine(mount_path: str):
    """Initialize the workflow engine."""
    global engine
    engine = WorkflowEngine('config.json', mount_path)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python app.py <mount_path>")
        sys.exit(1)
    
    mount_path = sys.argv[1]
    init_engine(mount_path)
    
    app.run(debug=True, host='0.0.0.0', port=5123)
