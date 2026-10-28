# Home Assistant dashboard

The homelab dashboard builds RAVN tiles from three pieces: a container template, a service template, and a small script that groups containers under a machine heading.

Icons are files in `config/www/service-icons/` on Home Assistant. A copy of that folder is in [service-icons](service-icons/) in this repository. A container named `esphome` looks for `/local/service-icons/esphome.png`.

## Cards to install

Install these from HACS before adding the templates:

- [button-card](https://github.com/custom-cards/button-card)
- [decluttering-card](https://github.com/custom-cards/decluttering-card)
- [vertical-stack-in-card](https://github.com/ofekashery/vertical-stack-in-card)
- [auto-entities](https://github.com/thomasloven/lovelace-auto-entities)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)

## ravn_container

One Docker container. The top of the tile opens a URL. The bottom bar starts and stops it.

The tile is 80 pixels tall: 72 for the icon and name, 8 for the bar. A running container is full color, and the bar is green (`#00AD3A`), including its edge. A stopped container shows the icon and name faded and grey, the bar is dark (`#262626`), and that edge uses the same border as the rest of the tile.

```yaml
type: custom:decluttering-card
template: ravn_container
variables:
  - entity: switch.ravn_esphome_running
  - web_url: https://esphome.local.p-wave.de
  - name: ESPHome
```

`entity` is the container's Running switch. `name` is optional. When it is set, that text is the label and the icon file name. When it is empty, the label and the icon file come from the entity id. `web_url` is opened on tap.

The listing script below fills these from **Custom URL** and **Custom name** on the container.

## ravn_service

A link tile with no container behind it. It does not start or stop anything. The tile is 80 pixels tall, and the icon and name are centered. The hover covers the whole tile.

```yaml
type: custom:decluttering-card
template: ravn_service
variables:
  - name: Grok Build
  - ico_url: /local/service-icons/grok.png
  - web_url: https://grok.local.p-wave.de
```

`ico_url` is the picture path. It is not guessed from the name.

## Machine heading

`server_heading` is a button-card template. It shows the machine name in bold and the address beside it, with no icon and no card background.

```yaml
type: custom:button-card
template: server_heading
custom_fields:
  name: dalaran
  ip: 192.168.178.210
```

## Container listing

An auto-entities card prints one heading and one 4-column grid for each machine. It uses every RAVN Running switch except `arcane_agent`, and it skips a container whose **Exclude container** switch is on.

The machine name comes from the matching status sensor, attribute `environment`, for example `dalaran • 192.168.178.210`. For `switch.ravn_esphome_running` that sensor is `sensor.ravn_esphome_status`.

End the template with `{{ ns.cards }}`. Do not pipe it through `to_json`. Home Assistant reads the result as a Python value, and `to_json` turns the grid flag `false` into text that the card then splits apart.

```yaml
type: custom:auto-entities
card:
  type: vertical-stack
card_param: cards
show_empty: false
filter:
  template: |
    {% set ns = namespace(rows=[]) %}
    {% for s in states.switch
      | selectattr('entity_id', 'match', '^switch\.ravn_.*_running$')
      | rejectattr('entity_id', 'search', 'arcane_agent') %}
      {% set base = s.entity_id | replace('switch.', '') | regex_replace('_running$', '') %}
      {% if is_state('switch.' ~ base ~ '_exclude_container', 'on') %}
        {% continue %}
      {% endif %}
      {% set env = state_attr('sensor.' ~ base ~ '_status', 'environment') or 'Other' %}
      {% set parts = env.split(' • ') %}
      {% set url_entity = 'text.' ~ base ~ '_custom_url' %}
      {% set name_entity = 'text.' ~ base ~ '_custom_name' %}
      {% set custom_url = states(url_entity) %}
      {% set custom_name = states(name_entity) %}
      {% set url = custom_url if custom_url not in ['unknown', 'unavailable', 'none', ''] else 'http://192.168.1.100:9000' %}
      {% set variables = [{'entity': s.entity_id}, {'web_url': url}] %}
      {% if custom_name not in ['unknown', 'unavailable', 'none', ''] %}
        {% set variables = variables + [{'name': custom_name}] %}
      {% endif %}
      {% set ns.rows = ns.rows + [{
        'sort': env ~ ' ' ~ s.entity_id,
        'env': env,
        'host': parts[0],
        'ip': parts[1] if parts | length > 1 else '',
        'card': {
          'type': 'custom:decluttering-card',
          'template': 'ravn_container',
          'variables': variables
        }
      }] %}
    {% endfor %}
    {% set rows = ns.rows | sort(attribute='sort') %}
    {% set ns = namespace(cards=[], last='') %}
    {% set bucket = namespace(cards=[]) %}
    {% for row in rows %}
      {% if row.env != ns.last %}
        {% if bucket.cards %}
          {% set ns.cards = ns.cards + [{'type': 'grid', 'columns': 4, 'square': false, 'cards': bucket.cards}] %}
        {% endif %}
        {% set bucket.cards = [] %}
        {% set ns.last = row.env %}
        {% set ns.cards = ns.cards + [{
          'type': 'custom:button-card',
          'template': 'server_heading',
          'custom_fields': {'name': row.host, 'ip': row.ip}
        }] %}
      {% endif %}
      {% set bucket.cards = bucket.cards + [row.card] %}
    {% endfor %}
    {% if bucket.cards %}
      {% set ns.cards = ns.cards + [{'type': 'grid', 'columns': 4, 'square': false, 'cards': bucket.cards}] %}
    {% endif %}
    {{ ns.cards }}
```

The fallback URL `http://192.168.1.100:9000` is used only when that container has no Custom URL.
