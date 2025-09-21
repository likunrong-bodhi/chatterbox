import os
import sys
import importlib.util
import threading
import queue
import time
import traceback
from i18n import install, available_languages
from utils import get_audio_files
import gradio as gr

# Dynamically import srt_tts.py
spec = importlib.util.spec_from_file_location("srt_tts", os.path.join(os.path.dirname(__file__), "srt_tts.py"))
srt_tts = importlib.util.module_from_spec(spec)
sys.modules["srt_tts"] = srt_tts
spec.loader.exec_module(srt_tts)

_ = install("en").gettext

def extract_path(file_obj):
    if file_obj is None:
        return None
    if isinstance(file_obj, dict):
        if 'path' in file_obj:
            return file_obj['path']
        if 'name' in file_obj:
            return file_obj['name']
    if hasattr(file_obj, 'name'):
        return file_obj.name
    if isinstance(file_obj, str):
        return file_obj
    return None

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

def run_srt_tts(input_file, reference_audio, language_id, multilingual, progress=gr.Progress(track_tqdm=True)):
    def generator():
        logs = ""
        output_wav = None
        catcher = StreamCatcher()
        try:
            input_path = extract_path(input_file)
            ref_path = extract_path(reference_audio)
            if not input_path:
                logs += "No input file selected or file path could not be determined."
                yield gr.update(value=f"<pre>{logs}</pre>"), None, gr.update(interactive=True)
                return
            srt_dir = os.path.dirname(input_path)
            srt_stem = os.path.splitext(os.path.basename(input_path))[0]
            reference_stem = os.path.splitext(os.path.basename(ref_path))[0] if ref_path else 'noRef'
            out_wav = os.path.join(srt_dir, f"{srt_stem}_tts_{reference_stem}.wav")

            def process():
                try:
                    srt_tts.run_srt_tts(
                        input_path=input_path,
                        reference_audio=ref_path,
                        language_id=language_id,
                        multilingual=multilingual,
                        out_wav=out_wav,
                        log_callback=lambda msg: catcher.q.put(msg),
                    )
                    catcher.q.put(('__RESULT__', out_wav))
                except Exception as e:
                    catcher.q.put(f"Error: {str(e)}\n{traceback.format_exc()}\n")
                    catcher.q.put(('__RESULT__', None))

            logs = ""
            output_wav = None
            with catcher:
                t = threading.Thread(target=lambda: process())
                # Instead of using a lambda to put result in queue, just let StreamCatcher capture all prints
                t.start()
                while t.is_alive() or not catcher.q.empty():
                    while not catcher.q.empty():
                        chunk = catcher.q.get()
                        if isinstance(chunk, tuple) and chunk[0] == '__RESULT__':
                            output_wav = chunk[1]
                            continue
                        logs += chunk
                        yield gr.update(value=f"<pre>{logs}</pre>"), None, gr.update(interactive=False)
                    time.sleep(0.5)
                t.join()
            yield gr.update(value=f"<pre>{logs}</pre>"), output_wav, gr.update(interactive=True)
        except Exception as e:
            logs += f"Error: {str(e)}\n{traceback.format_exc()}\n"
            yield gr.update(value=f"<pre>{logs}</pre>"), None, gr.update(interactive=True)
    return generator

def gradio_ui(target_dir):
    def on_change_language(lang):
        trans = install(lang)
        _ = trans.gettext
        return (
            gr.update(value=_('# Chatterbox SRT/TXT TTS Tool')),
            gr.update(label=_('Input SRT/TXT File')),
            gr.update(value=_('Refresh Reference Files List')),
            gr.update(label=_('Reference Audio (from target dir)')),
            gr.update(label=_('Language')),
            gr.update(value=_('Run TTS')),
            gr.update(label=_('Result')),
            gr.update(label=_('Language'))
        )

    def on_refresh_files(_click=None):
        return gr.update(choices=get_audio_files(target_dir))

    with gr.Blocks() as demo:
        with gr.Row():
            with gr.Column(scale=2):
                title_md = gr.Markdown(_('# Chatterbox SRT/TXT TTS Tool'))
            with gr.Column(scale=1):
                lang_dd = gr.Dropdown(
                    choices=available_languages(),
                    value="en",
                    label=_('Language'),
                    scale=0
                )
        input_file = gr.File(label=_('Input SRT/TXT File'), file_types=[".srt", ".txt"])
        refresh_btn = gr.Button(value=_('Refresh Reference Files List'))
        reference_audio = gr.Dropdown(
            label=_('Reference Audio (from target dir)'),
            choices=get_audio_files(target_dir),
            interactive=True
        )
        language_choices = [
            "Arabic (ar)", "Danish (da)", "German (de)", "Greek (el)", "English (en)", "Spanish (es)",
            "Finnish (fi)", "French (fr)", "Hebrew (he)", "Hindi (hi)", "Italian (it)", "Japanese (ja)",
            "Korean (ko)", "Malay (ms)", "Dutch (nl)", "Norwegian (no)", "Polish (pl)", "Portuguese (pt)",
            "Russian (ru)", "Swedish (sv)", "Swahili (sw)", "Turkish (tr)", "Chinese (zh)"
        ]
        language_id = gr.Dropdown(
            label=_('Language'),
            choices=language_choices,
            value="English (en)",
            interactive=True
        )
        run_btn = gr.Button(value=_('Run TTS'))
        result = gr.HTML(label=_('Result'), elem_id="result_html")
        try:
            download = gr.File(label="Download Output")
        except Exception:
            download = gr.File(label="Download Output")
        gr.HTML("""
        <style>
        #result_html {
            min-height: 200px;
        }
        footer, .svelte-1ipelgc, .svelte-1ipelgc *, .gradio-container .fixed.bottom-4.right-4, .gradio-container .fixed.bottom-4.left-4 {
            display: none !important;
        }
        </style>
        """)

        # LocalStorage load
        demo.load(
            fn=None,
            inputs=None,
        outputs=[lang_dd, reference_audio, language_id],
            js="""
        () => {
            const lang = localStorage.getItem('tts_lang') || "en";
            const ref = localStorage.getItem('tts_reference_audio') || null;
            const langid = localStorage.getItem('tts_language_id') || "en";
            const multi = localStorage.getItem('tts_multilingual') === 'true';
            return [lang, ref, langid, multi];
        }
        """
        )
        lang_dd.change(
            on_change_language,
            inputs=lang_dd,
            outputs=[title_md, input_file, refresh_btn, reference_audio, language_id, run_btn, result, lang_dd]
        )
        lang_dd.change(
            fn=None, inputs=lang_dd, outputs=None,
            js="(v) => { if (v !== undefined && v !== null) localStorage.setItem('tts_lang', v); }"
        )
        reference_audio.change(
            fn=None, inputs=reference_audio, outputs=None,
            js="(v) => { if (v !== undefined && v !== null) localStorage.setItem('tts_reference_audio', v); }"
        )
        language_id.change(
            fn=None, inputs=language_id, outputs=None,
            js="(v) => { localStorage.setItem('tts_language_id', v ?? ''); }"
        )
        refresh_btn.click(
            on_refresh_files,
            inputs=None,
            outputs=reference_audio
        )

        def on_run(input_file, reference_audio, language_id_combo):
            if not input_file or not language_id_combo:
                yield gr.update(value=_('Please provide all required fields.')), None, gr.update(interactive=True)
                return
            # Extract the 2-char code from the combo box value
            import re
            m = re.search(r'\((..)\)', language_id_combo)
            lang_code = m.group(1) if m else 'en'
            multilingual = lang_code != 'en'
            target_voice_path = os.path.join(target_dir, reference_audio) if reference_audio else None
            gen = run_srt_tts(input_file, target_voice_path, lang_code, multilingual)
            for update in gen():
                result_html, output_wav, run_btn_state = update
                if output_wav:
                    download_update = gr.update(value=output_wav)
                else:
                    download_update = gr.update(value=None)
                yield result_html, download_update, run_btn_state

        run_btn.click(
            on_run,
            inputs=[input_file, reference_audio, language_id],
            outputs=[result, download, run_btn],
            preprocess=False,
            show_progress=True,
            queue=True,
        )
    return demo

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Chatterbox Gradio SRT/TXT TTS UI")
    parser.add_argument('--target_dir', type=str, required=True, help='Directory containing target wav/mp3 files')
    args = parser.parse_args()
    demo = gradio_ui(args.target_dir)
    demo.launch(share=False, inbrowser=True)
