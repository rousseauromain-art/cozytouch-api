async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        # 1. On crée un dictionnaire de toutes les températures trouvées sur le compte
        all_temperatures = {}
        for d in devices:
            # On cherche dans TOUS les appareils du compte
            for s in d.states:
                if s.name in ["core:TemperatureState", "io:MiddleWaterTemperatureState"]:
                    # On stocke la température trouvée avec une clé liée à l'URL de l'appareil
                    # Souvent le capteur a une URL proche du radiateur (ex: #2 au lieu de #1)
                    base_url = d.device_url.split('#')[0]
                    all_temperatures[base_url] = s.value

        res = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                s = {state.name: state.value for state in d.states}
                base_url = d.device_url.split('#')[0]
                
                eff = s.get("io:EffectiveTemperatureSetpointState", "?")
                
                # 2. On cherche la température ambiante 
                # Soit dans le radiateur, soit dans un capteur qui partage la même base d'URL
                ambient = s.get("core:TemperatureState") 
                if ambient is None:
                    ambient = all_temperatures.get(base_url, "Inconnue")

                rate = s.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ Consigne: <b>{eff}°C</b>\n"
                line += f"└ T° Ambiante: <b>{ambient}°C</b>\n"
                line += f"└ Activité: {rate}%"
                res.append(line)
        
        return "\n\n".join(res)
