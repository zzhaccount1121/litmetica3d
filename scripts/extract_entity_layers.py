"""Offline extraction with a user-provided named client and dependency folder.

Requires the Java version declared by Minecraft (26.2: Java 25). Dependencies
are the original game's libraries, NOT downloaded or shipped by this script.
"""
import argparse
from pathlib import Path
import subprocess
import os


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jar',type=Path,required=True)
    p.add_argument('--java-home',type=Path,required=True)
    p.add_argument('--libraries',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    jars=sorted(a.libraries.resolve().rglob('*.jar'))
    cp=os.pathsep.join(map(str,[a.jar.resolve(),out,*jars]))
    suffix='.exe' if os.name=='nt' else ''
    source=Path(__file__).with_name('ExtractModels.java')
    subprocess.run([str(a.java_home/'bin'/('javac'+suffix)),'-encoding','UTF-8','-cp',cp,'-d',str(out),str(source)],check=True)
    subprocess.run([str(a.java_home/'bin'/('java'+suffix)),'-Djava.awt.headless=true','-cp',cp,'ExtractModels',str(out/'entity_layers.json')],cwd=out,check=True)


if __name__=='__main__':
    main()
