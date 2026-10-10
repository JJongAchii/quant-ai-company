"""Local design demo: macOS speech or user-supplied audio; never calls a paid provider or uploads."""

import argparse
import json
import shutil
from pathlib import Path

from quant_company.config import Settings
from quant_company.video.contracts import VideoPlan
from quant_company.video.render import Renderer, run, spoken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.local/daily-video-demo'))
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe', default=shutil.which('ffprobe') or '')
    parser.add_argument('--alignment-model', default='small')
    parser.add_argument('--audio', type=Path, nargs=3, help='Three local narration files for the demo script')
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error('Install FFmpeg or provide --ffmpeg')
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    scene_data = [
        ('오늘의 핵심', ['글이 먼저 보이는 시장 브리핑', '한 화면에 하나의 핵심 문장'],
         '이 영상은 실제 투자 정보가 아닌 화면 검토용 샘플입니다. 핵심 문장을 크게 보여 주고, 설명은 차분한 목소리로 전달합니다.'),
        ('해석에는 조건을 함께', ['가능한 연결을 설명합니다', '다른 설명과 확인 조건도 함께 제시합니다'],
         '사실과 해석은 구분해서 전달합니다. 가능한 연결을 설명하고, 반대 근거와 아직 확인해야 할 조건을 같은 화면에서 보여 줍니다.'),
        ('게시 전 사람이 검토', ['자료 기준과 출처를 남깁니다', '비공개 영상을 확인한 뒤 공개를 승인합니다'],
         '자료의 기준 시각과 출처를 남깁니다. 완성된 영상은 비공개로 먼저 올리고, 사람이 전체 영상을 확인한 뒤 공개를 승인합니다.')]
    scenes = [{'heading': h, 'lines': lines, 'narration': narration, 'claim_ids': [f'demo-{i}']}
              for i, (h, lines, narration) in enumerate(scene_data)]
    plan = VideoPlan(title='뭐든story 아침 브리핑 화면 샘플', thumbnail='문장이 중심이 되는 브리핑',
                     introduction='합성 자료를 사용한 화면 샘플입니다. 투자 정보가 아닙니다.', scenes=scenes,
                     pinned_comment='글의 크기와 읽는 속도를 확인해 주세요.')
    paths = args.audio
    if not paths:
        if not shutil.which('say'):
            parser.error('Provide --audio on systems without macOS speech')
        paths = []
        for i, scene in enumerate(plan.scenes):
            path = directory/f'speech-{i:02}.aiff'
            run(['say', '-v', 'Yuna', '-r', '180', '-o', str(path), spoken(scene.narration)])
            paths.append(path)
    claims = [{'id': f'demo-{i}', 'text': s.narration, 'evidence': []} for i, s in enumerate(plan.scenes)]
    settings = Settings(video_ffmpeg=args.ffmpeg, video_ffprobe=args.ffprobe, video_alignment_model=args.alignment_model)
    manifest = Renderer(settings).render({'source': {'proposal': {'summary': claims}, 'bundle': {'documents': []},
                    'day': '합성 자료', 'cutoff': '디자인 샘플 · 투자 정보 아님'}}, plan, directory, paths)
    print(json.dumps({'video': str(directory/'video.mp4'), 'duration': manifest['duration'], 'digest': manifest['digest'],
                      'voice': 'local macOS or supplied audio', 'uploaded': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
