import gradio as gr
import os
import sys
import tempfile
import shutil
import subprocess
import importlib.util

# Import process_audio_files from dir_vc.py dynamically
spec = importlib.util.spec_from_file_location("dir_vc", os.path.join(os.path.dirname(__file__), "dir_vc.py"))
dir_vc = importlib.util.module_from_spec(spec)
sys.modules["dir_vc"] = dir_vc
spec.loader.exec_module(dir_vc)


def get_audio_files(directory):
    if not os.path.isdir(directory):
        return []
    return [f for f in os.listdir(directory) if f.lower().endswith(('.wav', '.mp3'))]


# Streaming log output to Gradio UI
import threading
import queue
import time
import contextlib
import sys

class StreamCatcher:
    def __init__(self):
        self.q = queue.Queue()
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
    def write(self, data):
        self.q.put(data)
    def flush(self):
        pass
    def __enter__(self):
        sys.stdout = self
        sys.stderr = self
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr

def run_voice_conversion(input_dir, output_dir, target_voice_path, progress=gr.Progress(track_tqdm=True)):
    def generator():
        catcher = StreamCatcher()
        logs = ""
        output_files = []
        try:
            with catcher:
                t = threading.Thread(target=lambda: output_files.extend(
                    dir_vc.process_audio_files(input_dir, output_dir, target_voice_path, continue_job=False)
                ))
                t.start()
                while t.is_alive() or not catcher.q.empty():
                    while not catcher.q.empty():
                        chunk = catcher.q.get()
                        logs += chunk
                        # 返回顺序要和 outputs 对齐（见第 3 步），downloads 用 gr.update() 占位
                        yield (
                            gr.update(value=f"<pre>{logs}</pre>"),  # result (HTML)
                            gr.update(value=None),                  # downloads (Files) —— 只占位
                            gr.update(interactive=False)            # run_btn
                        )
                    time.sleep(1)
                t.join()
            
            # ⬇️ 处理完成：同时更新 HTML 和 downloads
            if not output_files:
                msg = f"No output files generated.\n<pre>{logs}</pre>"
                yield gr.update(value=msg), gr.update(value=[]), gr.update(interactive=True)
            else:
                abs_paths = [os.path.abspath(f) for f in output_files]
                links_html = "<br>".join(f"<div>{p}</div>" for p in abs_paths)
                result_html = f"<h2>Processing complete.</h2> Output files:<br>{links_html}<br><pre>{logs}</pre>"

                # ✅ downloads 传入“文件路径列表”
                yield (
                    gr.update(value=result_html),        # result
                    gr.update(value=abs_paths),          # ✅ downloads 用“文件路径列表”
                    gr.update(interactive=True)          # run_btn
                )
        except Exception as e:
            err = f"Error: {str(e)}\n<pre>{logs}</pre>"
            # 失败时清空 downloads
            yield gr.update(value=err), gr.update(value=[]), gr.update(interactive=True)
    return generator

def update_target_files(target_dir):
    files = get_audio_files(target_dir)
    return gr.update(choices=files, value=files[0] if files else None)


def gradio_ui(target_dir):
    with gr.Blocks() as demo:
        gr.Markdown("# Chatterbox Voice Conversion Batch Tool")
        # Add JS for localStorage persistence of input_dir, output_dir, and target_file
        input_dir = gr.Textbox(label="Input File or Directory", placeholder="Path to input audio file or directory with audio files")
        output_dir = gr.Textbox(label="Output Directory", placeholder="Path to save output files")
        refresh_btn = gr.Button("Refresh Target Files List")
        target_file = gr.Dropdown(label="Target Voice File (from target dir)", choices=get_audio_files(target_dir), interactive=True)
        run_btn = gr.Button("Run Voice Conversion")

        result = gr.HTML(label="Result", elem_id="result_html")
        # Add custom CSS for minimum height using a style HTML block
        
        # ✅ 新增：用于多文件下载的组件（Gradio 4 推荐用 gr.Files）
        try:
            downloads = gr.Files(label="Download Outputs")  # Gradio 4+
        except Exception:
            # 如果你在 Gradio 3，可退化为 gr.File(file_count="multiple")
            downloads = gr.File(label="Download Outputs", file_count="multiple")

        gr.HTML("""
        <style>
        #result_html {
            min-height: 200px;
        }
        </style>
        """)

        # 1) On load: restore values from localStorage into the components
        demo.load(
            fn=None,
            inputs=None,
            outputs=[input_dir, output_dir, target_file],
            js="""
        () => {
        const inDir = localStorage.getItem('vc_input_dir') || "";
        const outDir = localStorage.getItem('vc_output_dir') || "";
        const target = localStorage.getItem('vc_target_file') || null;
        return [inDir, outDir, target];
        }
        """
        )

        # 2) On change: persist values back to localStorage
        input_dir.change(
            fn=None, inputs=input_dir, outputs=None,
            js="(v) => { localStorage.setItem('vc_input_dir', v ?? ''); }"
        )
        output_dir.change(
            fn=None, inputs=output_dir, outputs=None,
            js="(v) => { localStorage.setItem('vc_output_dir', v ?? ''); }"
        )
        target_file.change(
            fn=None, inputs=target_file, outputs=None,
            js="(v) => { if (v !== undefined && v !== null) localStorage.setItem('vc_target_file', v); }"
        )

        def on_refresh():
            return update_target_files(target_dir)

        refresh_btn.click(on_refresh, inputs=[], outputs=[target_file])

        def on_run(input_dir, output_dir, target_file):
            if not input_dir or not output_dir or not target_file:
                # result / downloads / run_btn
                yield gr.update(value="Please provide all required fields."), gr.update(value=[]), gr.update(interactive=True)
                return
            target_voice_path = os.path.join(target_dir, target_file)
            gen = run_voice_conversion(input_dir, output_dir, target_voice_path)
            for update in gen():
                yield update

        run_btn.click(
            on_run,
            inputs=[input_dir, output_dir, target_file],
            outputs=[result, downloads, run_btn],
            preprocess=False,
            show_progress=True,
            queue=True,
        )

    return demo

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Chatterbox Gradio VC Batch UI")
    parser.add_argument('--target_dir', type=str, required=True, help='Directory containing target wav/mp3 files')
    args = parser.parse_args()
    demo = gradio_ui(args.target_dir)
    demo.launch(share=False, inbrowser=True)
    
