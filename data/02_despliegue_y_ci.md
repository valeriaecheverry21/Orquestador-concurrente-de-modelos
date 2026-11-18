# Manual técnico de Atlas — 2. Despliegue e integración continua

Este capítulo describe cómo llega un cambio desde el repositorio hasta producción, qué controles automáticos debe superar y cómo revertirlo si algo sale mal.

## 2.1 Flujo de ramas

El equipo trabaja con desarrollo basado en tronco (trunk-based development). La rama principal se llama `main` y siempre debe poder desplegarse. Las ramas de trabajo deben ser cortas: una rama que lleva más de 3 días sin fusionarse se considera un riesgo y se comenta en la reunión diaria.

Cada cambio entra a `main` mediante un pull request. Un pull request necesita:

- Al menos una aprobación de otra persona del equipo. Los cambios en `atlas-gateway` o en esquemas de base de datos necesitan dos aprobaciones.
- Que todos los controles de integración continua estén en verde.
- Una descripción que explique el motivo del cambio y cómo se probó.

Los títulos de los commits siguen la convención Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`). El número de versión de cada servicio se calcula automáticamente a partir de esos prefijos.

## 2.2 Integración continua

La integración continua corre en GitHub Actions. Cada pull request ejecuta, en este orden:

1. Análisis estático y formato (ruff y mypy para Python, golangci-lint para Go).
2. Pruebas unitarias. La cobertura mínima aceptada es del 80 % de líneas por servicio; si un cambio baja la cobertura por debajo de ese umbral, el control falla.
3. Pruebas de contrato entre servicios, que verifican que los mensajes publicados en Kafka cumplen el esquema registrado.
4. Escaneo de dependencias en busca de vulnerabilidades conocidas. Una vulnerabilidad de severidad crítica bloquea la fusión; las de severidad alta generan una advertencia y deben resolverse en un plazo de 14 días.
5. Construcción de la imagen de contenedor y publicación en el registro interno con la etiqueta del commit.

Si una prueba falla de forma intermitente (prueba inestable o "flaky"), no se debe reintentar indefinidamente. La persona que la detecta la marca como inestable, abre un ticket y el equipo dueño del servicio tiene 5 días hábiles para corregirla o eliminarla.

## 2.3 Despliegue a staging y producción

Cada fusión en `main` despliega automáticamente a staging. El despliegue a producción no es automático: lo inicia una persona del equipo desde el pipeline, después de que el cambio haya pasado al menos 24 horas en staging sin alertas.

Producción se actualiza con un despliegue canario:

1. La nueva versión recibe el 5 % del tráfico durante 15 minutos.
2. Si la tasa de errores HTTP 5xx de la versión canaria no supera el 1 % y la latencia p95 no empeora más de un 20 % respecto de la versión estable, se sube al 50 % durante otros 15 minutos.
3. Si los indicadores siguen sanos, la nueva versión pasa al 100 %.

Si alguno de los umbrales se supera en cualquier etapa, el pipeline revierte automáticamente el canario y notifica en el canal #atlas-deploys.

## 2.4 Ventanas de despliegue y congelamientos

Los despliegues a producción solo se hacen de lunes a jueves, entre las 09:00 y las 16:00, hora de Santiago. No se despliega los viernes, los fines de semana ni los feriados, para que siempre haya personas disponibles si algo falla.

Durante la campaña de fin de año hay un congelamiento total de despliegues entre el 20 de diciembre y el 3 de enero. En ese período solo se aceptan correcciones urgentes.

## 2.5 Correcciones urgentes (hotfix)

Una corrección urgente es un cambio que resuelve un incidente SEV1 o SEV2 en curso. Las correcciones urgentes pueden saltarse las 24 horas en staging y la ventana de despliegue, pero nunca pueden saltarse la revisión de código: necesitan la aprobación del líder técnico de guardia o, en su ausencia, de la persona responsable del incidente. Toda corrección urgente debe mencionarse en el postmortem del incidente.

## 2.6 Reversión (rollback)

Revertir es siempre la primera opción ante un despliegue defectuoso: primero se restaura el servicio y después se investiga la causa. Para revertir, se vuelve a desplegar la etiqueta de la versión anterior desde el pipeline con la acción "Rollback"; la operación tarda menos de 5 minutos.

Las migraciones de base de datos deben ser compatibles hacia atrás durante al menos una versión, de modo que la versión anterior del servicio siga funcionando con el esquema nuevo. Por eso las columnas nunca se eliminan en la misma versión en que dejan de usarse: se eliminan como mínimo dos versiones después.
