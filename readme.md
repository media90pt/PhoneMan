# PhoneMan Linux

Backup de contactos, chamadas e SMS do telemóvel Android para o Linux via `adb`.

## Funcionalidades

- ✅ Backup de **contactos** (exporta `.vcf` com UID para dedupe)
- ✅ Backup de **chamadas** (exporta `.csv` com tipo, duração, data)
- ✅ Backup de **SMS** (exporta `.csv` com corpo da mensagem)
- ✅ Backup completo (os três de uma vez)
- ✅ Ficheiros flat com timestamp (sem subpastas)
- ✅ Notificações desktop (`notify-send`)
- ✅ Detecção de estado do dispositivo (`device`, `unauthorized`, `offline`)
- ✅ Interface gráfica em Tkinter (sem dependências pesadas)

## Requisitos

| Componente | Instalação |
|---|---|
| Python 3.8+ | `sudo apt install python3` |
| Tkinter | `sudo apt install python3-tk` |
| ADB | `sudo apt install adb` |
| Notificações (opcional) | `sudo apt install libnotify-bin` |

## Instalação

```bash
git clone https://github.com/teu-username/phoneman.git
cd phoneman   
